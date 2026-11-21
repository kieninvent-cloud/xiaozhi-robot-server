import asyncio
import difflib
import glob
import hashlib
import os
import re
import sys
import traceback
from typing import TYPE_CHECKING

from core.providers.tts.dto.dto import ContentType, SentenceType, TTSMessageDTO
from plugins_func.register import Action, ActionResponse, ToolType, register_function

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__

AUDIO_EXTS = (".mp3", ".m4a", ".opus", ".ogg", ".wav", ".p3")

play_music_function_desc = {
    "type": "function",
    "function": {
        "name": "play_music",
        "description": "Gọi công cụ này khi người dùng muốn nghe nhạc, mở bài hát bất kỳ (nhạc Việt, nhạc trẻ, remix, lofi...).",
        "parameters": {
            "type": "object",
            "properties": {
                "song_name": {
                    "type": "string",
                    "description": "Tên bài hát chuẩn tiếng Việt (kèm tên ca sĩ nếu biết, ví dụ 'Nơi Này Có Anh Sơn Tùng M-TP', 'Sóng Gió Jack'). Nếu người dùng chỉ nói 'mở nhạc' mà không nêu tên bài cụ thể thì đặt là 'nhạc trẻ Việt Nam hay nhất'.",
                }
            },
            "required": ["song_name"],
        },
    },
}


@register_function("play_music", play_music_function_desc, ToolType.SYSTEM_CTL)
async def play_music(conn: "ConnectionHandler", song_name: str):
    try:
        query = (song_name or "").strip()
        if not query or query.lower() == "random":
            query = "nhạc trẻ Việt Nam hay nhất"
        await handle_online_or_local_music(conn, query)
        return ActionResponse(
            action=Action.RECORD,
            result=f"Đang phát bài hát: {query}",
            response=f"Đang mở bài {query} cho bạn nhé",
        )
    except Exception as e:
        conn.logger.bind(tag=TAG).error(f"Lỗi phát nhạc: {e}\n{traceback.format_exc()}")
        return ActionResponse(
            action=Action.RESPONSE,
            result=str(e),
            response="Xin lỗi, mình chưa tìm thấy bài hát này. Bạn thử nói rõ tên bài hát và ca sĩ nhé.",
        )


def _get_music_dir(conn: "ConnectionHandler") -> str:
    plugins_config = conn.config.get("plugins", {})
    music_dir = plugins_config.get("play_music", {}).get("music_dir", "./music")
    abs_dir = os.path.abspath(music_dir)
    os.makedirs(abs_dir, exist_ok=True)
    return abs_dir


def _find_cached_file(query: str, music_dir: str):
    music_files = [
        f for f in os.listdir(music_dir) if f.lower().endswith(AUDIO_EXTS)
    ]
    if not music_files:
        return None
    best_match = None
    best_ratio = 0.0
    q_low = query.lower()
    for f in music_files:
        name = os.path.splitext(f)[0].lower()
        if q_low in name:
            return os.path.join(music_dir, f)
        ratio = difflib.SequenceMatcher(None, q_low, name).ratio()
        if ratio > best_ratio and ratio > 0.65:
            best_ratio = ratio
            best_match = os.path.join(music_dir, f)
    return best_match


def _cleanup_old_cache(music_dir: str, max_files: int = 15):
    files = [
        os.path.join(music_dir, f)
        for f in os.listdir(music_dir)
        if f.lower().endswith(AUDIO_EXTS)
    ]
    if len(files) <= max_files:
        return
    files.sort(key=lambda p: os.path.getmtime(p))
    for old_file in files[: len(files) - max_files]:
        try:
            os.remove(old_file)
        except OSError:
            pass


async def _run_yt_dlp(search_prefix: str, query: str, music_dir: str, base_name: str, cookies_path: str | None, logger):
    out_template = os.path.join(music_dir, f"{base_name}.%(ext)s")

    # Use scsearch5 + --max-downloads 1 so if #1 is a 30s Go+ preview, it automatically grabs #2!
    cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--quiet",
        "--no-warnings",
        "--no-playlist",
        "--force-overwrites",
        "--socket-timeout",
        "10",
        "--retries",
        "2",
        "--max-downloads",
        "1",
        "--match-filter",
        "duration >= 45 & duration <= 480",
        "-f",
        "bestaudio[ext=mp3]/bestaudio[abr<=128]/bestaudio",
        "-x",
        "--audio-format",
        "mp3",
        "--audio-quality",
        "5",
        "--default-search",
        search_prefix,
        "-o",
        out_template,
        query,
    ]
    if cookies_path and os.path.exists(cookies_path):
        cmd.extend(["--cookies", cookies_path])

    logger.bind(tag=TAG).info(f"Đang tải nhanh ({search_prefix}): {query}")
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=25)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError(f"Tải nhạc ({search_prefix}) quá thời gian chờ")

    matches = [
        p for p in glob.glob(os.path.join(music_dir, f"{base_name}.*"))
        if p.lower().endswith(AUDIO_EXTS)
    ]
    # Note: yt-dlp returns exit code 101 when --max-downloads 1 is reached (success!)
    if proc.returncode not in (0, 101) or not matches:
        err_msg = stderr.decode("utf-8", errors="ignore")[-300:]
        raise RuntimeError(f"{search_prefix} lỗi ({proc.returncode}): {err_msg}")

    return matches[0]


async def _download_online_music(query: str, music_dir: str, logger):
    safe_slug = re.sub(r"[^\w\s-]", "", query, flags=re.UNICODE).strip()[:40]
    short_hash = hashlib.md5(query.encode("utf-8")).hexdigest()[:6]
    base_name = f"{safe_slug}_{short_hash}" if safe_slug else f"song_{short_hash}"

    existing = [
        p for p in glob.glob(os.path.join(music_dir, f"{base_name}.*"))
        if p.lower().endswith(AUDIO_EXTS)
    ]
    if existing:
        return existing[0]

    cookies_path = None
    for candidate in ("/etc/secrets/cookies.txt", os.path.join(music_dir, "cookies.txt")):
        if os.path.exists(candidate):
            cookies_path = candidate
            break

    is_cloud = bool(os.environ.get("RENDER") or os.environ.get("SPACE_HOST"))
    if is_cloud and not cookies_path:
        # Skip YouTube on Render Free without cookies (avoids wasting 15s on guaranteed bot block)
        sources = [("scsearch5", None)]
    elif cookies_path or not is_cloud:
        sources = [("ytsearch3", cookies_path), ("scsearch5", None)]
    else:
        sources = [("scsearch5", None)]

    last_err = None
    for prefix, cpath in sources:
        try:
            path = await _run_yt_dlp(prefix, query, music_dir, base_name, cpath, logger)
            _cleanup_old_cache(music_dir)
            return path
        except Exception as e:
            last_err = e
            logger.bind(tag=TAG).warning(f"Nguồn {prefix} không tải được: {e}")

    raise RuntimeError(str(last_err))


async def handle_online_or_local_music(conn: "ConnectionHandler", query: str):
    music_dir = _get_music_dir(conn)
    music_path = _find_cached_file(query, music_dir)
    if not music_path:
        music_path = await _download_online_music(query, music_dir, conn.logger)

    intro_text = f"Đang mở bài {query}, mời bạn cùng nghe nhé."
    conn.tts.store_tts_text(conn.sentence_id, intro_text)

    if conn.intent_type == "intent_llm":
        conn.tts.tts_text_queue.put(
            TTSMessageDTO(
                sentence_id=conn.sentence_id,
                sentence_type=SentenceType.FIRST,
                content_type=ContentType.ACTION,
            )
        )
    conn.tts.tts_text_queue.put(
        TTSMessageDTO(
            sentence_id=conn.sentence_id,
            sentence_type=SentenceType.MIDDLE,
            content_type=ContentType.TEXT,
            content_detail=intro_text,
        )
    )
    conn.tts.tts_text_queue.put(
        TTSMessageDTO(
            sentence_id=conn.sentence_id,
            sentence_type=SentenceType.MIDDLE,
            content_type=ContentType.FILE,
            content_file=music_path,
        )
    )
    if conn.intent_type == "intent_llm":
        conn.tts.tts_text_queue.put(
            TTSMessageDTO(
                sentence_id=conn.sentence_id,
                sentence_type=SentenceType.LAST,
                content_type=ContentType.ACTION,
            )
        )
