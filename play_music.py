import asyncio
import difflib
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

play_music_function_desc = {
    "type": "function",
    "function": {
        "name": "play_music",
        "description": "Gọi công cụ này khi người dùng muốn nghe nhạc, mở bài hát bất kỳ (từ YouTube hoặc thư viện nhạc).",
        "parameters": {
            "type": "object",
            "properties": {
                "song_name": {
                    "type": "string",
                    "description": "Tên bài hát (kèm ca sĩ nếu có). Nếu người dùng chỉ nói 'mở nhạc' mà không nêu tên bài cụ thể thì đặt là 'nhạc trẻ Việt Nam hay nhất'.",
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
        await handle_youtube_or_local_music(conn, query)
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
            response="Xin lỗi, mình không tìm hoặc tải được bài hát này trên YouTube.",
        )


def _get_music_dir(conn: "ConnectionHandler") -> str:
    plugins_config = conn.config.get("plugins", {})
    music_dir = plugins_config.get("play_music", {}).get("music_dir", "./music")
    abs_dir = os.path.abspath(music_dir)
    os.makedirs(abs_dir, exist_ok=True)
    return abs_dir


def _find_cached_file(query: str, music_dir: str):
    music_files = [
        f for f in os.listdir(music_dir) if f.lower().endswith((".mp3", ".wav", ".p3"))
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


def _cleanup_old_cache(music_dir: str, max_files: int = 30):
    files = [
        os.path.join(music_dir, f)
        for f in os.listdir(music_dir)
        if f.lower().endswith((".mp3", ".wav", ".p3"))
    ]
    if len(files) <= max_files:
        return
    files.sort(key=lambda p: os.path.getmtime(p))
    for old_file in files[: len(files) - max_files]:
        try:
            os.remove(old_file)
        except OSError:
            pass


async def _download_from_youtube(query: str, music_dir: str, logger):
    safe_slug = re.sub(r"[^\w\s-]", "", query, flags=re.UNICODE).strip()[:40]
    short_hash = hashlib.md5(query.encode("utf-8")).hexdigest()[:6]
    base_name = f"{safe_slug}_{short_hash}" if safe_slug else f"yt_{short_hash}"
    out_template = os.path.join(music_dir, f"{base_name}.%(ext)s")
    target_mp3 = os.path.join(music_dir, f"{base_name}.mp3")

    if os.path.exists(target_mp3):
        return target_mp3

    cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--quiet",
        "--no-warnings",
        "--no-playlist",
        "--force-overwrites",
        "-f",
        "bestaudio[abr<=128]/bestaudio",
        "-x",
        "--audio-format",
        "mp3",
        "--audio-quality",
        "5",
        "--default-search",
        "ytsearch1",
        "-o",
        out_template,
        query,
    ]
    cookies_path = os.path.join(music_dir, "cookies.txt")
    if os.path.exists(cookies_path):
        cmd.extend(["--cookies", cookies_path])

    logger.bind(tag=TAG).info(f"Đang tải nhạc từ YouTube: {query}")
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=40)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("Tải nhạc YouTube bị quá thời gian chờ")

    if proc.returncode != 0 or not os.path.exists(target_mp3):
        err_msg = stderr.decode("utf-8", errors="ignore")[-300:]
        raise RuntimeError(f"yt-dlp lỗi ({proc.returncode}): {err_msg}")

    _cleanup_old_cache(music_dir)
    return target_mp3


async def handle_youtube_or_local_music(conn: "ConnectionHandler", query: str):
    music_dir = _get_music_dir(conn)
    music_path = _find_cached_file(query, music_dir)
    if not music_path:
        music_path = await _download_from_youtube(query, music_dir, conn.logger)

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
