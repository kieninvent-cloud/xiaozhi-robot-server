import asyncio
import difflib
import glob
import hashlib
import os
import re
import sys
import threading
import traceback
from typing import TYPE_CHECKING

import yt_dlp

from core.providers.tts.dto.dto import ContentType, SentenceType, TTSMessageDTO
from plugins_func.register import Action, ActionResponse, ToolType, register_function

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__

AUDIO_EXTS = (".mp3", ".m4a", ".opus", ".ogg", ".wav", ".p3")

# Persistent shared YoutubeDL instance with pre-warmed SoundCloud client_id (~0.6s search + ~1.2s stream)
_YDL_LOCK = threading.Lock()
_SHARED_YDL = yt_dlp.YoutubeDL({
    "quiet": True,
    "no_warnings": True,
    "noplaylist": True,
    "extract_flat": "in_playlist",
    "format": "http_mp3_1_0/bestaudio[ext=mp3]/bestaudio[abr<=128]/bestaudio",
    "socket_timeout": 8,
})


def _warmup_ydl_bg():
    try:
        with _YDL_LOCK:
            _SHARED_YDL.extract_info("scsearch1:nhac tre", download=False)
    except Exception:
        pass


threading.Thread(target=_warmup_ydl_bg, daemon=True).start()

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
            response=f"Tôi đang tìm bài {query}, bạn chờ một chút nhé.",
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


def _resolve_stream_url_sync(query: str) -> str:
    """Resolve a direct SoundCloud HTTP MP3 stream URL in ~1.8s using the pre-warmed shared YoutubeDL instance."""
    with _YDL_LOCK:
        info = _SHARED_YDL.extract_info(f"scsearch5:{query}", download=False)
        entries = (info or {}).get("entries") or []
        chosen_url = None
        for entry in entries:
            if not entry:
                continue
            dur = entry.get("duration") or 0
            # Filter out 30s SoundCloud Go+ previews and >10min mixes
            if 45 <= dur <= 540:
                chosen_url = entry.get("url") or entry.get("webpage_url")
                break
        if not chosen_url and entries:
            first = entries[0] or {}
            chosen_url = first.get("url") or first.get("webpage_url")
        if not chosen_url:
            raise RuntimeError(f"Không tìm thấy bài hát phù hợp cho: {query}")

        track_info = _SHARED_YDL.extract_info(chosen_url, download=False)
        stream_url = (track_info or {}).get("url")
        if not stream_url and (track_info or {}).get("formats"):
            for fmt in reversed(track_info["formats"]):
                if fmt.get("url") and fmt.get("protocol") in ("http", "https"):
                    stream_url = fmt["url"]
                    break
            if not stream_url:
                stream_url = track_info["formats"][-1].get("url")
        if not stream_url:
            raise RuntimeError("Không lấy được luồng phát trực tiếp")
        return stream_url


async def _background_cache_download(query: str, music_dir: str, base_name: str, logger):
    """Download the MP3 in the background for instant future replays."""
    try:
        out_template = os.path.join(music_dir, f"{base_name}.%(ext)s")
        cmd = [
            sys.executable,
            "-m",
            "yt_dlp",
            "--quiet",
            "--no-warnings",
            "--no-playlist",
            "--max-downloads",
            "1",
            "--match-filter",
            "duration >= 45 & duration <= 540",
            "-f",
            "http_mp3_1_0/bestaudio[ext=mp3]/bestaudio[abr<=128]/bestaudio",
            "--default-search",
            "scsearch5",
            "-o",
            out_template,
            query,
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.communicate(), timeout=40)
        _cleanup_old_cache(music_dir)
    except Exception as e:
        logger.bind(tag=TAG).debug(f"Background music cache skipped: {e}")


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

    # Yield 0.45s so the TTS thread gets 100% of CPU on Render 0.1 vCPU to synthesize
    # and send the waiting announcement BEFORE yt-dlp runs!
    await asyncio.sleep(0.45)

    # Fast in-process stream URL extraction (~1.6s) so FFmpeg streams immediately!
    stream_url = await asyncio.to_thread(_resolve_stream_url_sync, query)
    stream_file = os.path.join(music_dir, f"{base_name}.stream")
    with open(stream_file, "w", encoding="utf-8") as f:
        f.write(stream_url)
    return stream_file


async def handle_online_or_local_music(conn: "ConnectionHandler", query: str):
    # 1. Speak the waiting announcement IMMEDIATELY (at t = 0.0s) while searching in parallel!
    # End with '.' (now recognized by TTS segmenter) so the whole sentence is synthesized in 1 single TTS call!
    wait_text = f"Tôi đang tìm bài {query}, bạn chờ một chút nhé."
    conn.tts.store_tts_text(conn.sentence_id, wait_text)

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
            content_detail=wait_text,
        )
    )

    # 2. While TTS is speaking the 3-second waiting message on the robot,
    #    resolve the cached file or direct stream URL (~1.6s).
    music_dir = _get_music_dir(conn)
    music_path = _find_cached_file(query, music_dir)
    if not music_path:
        music_path = await _download_online_music(query, music_dir, conn.logger)

    # 3. Enqueue the audio stream right behind the waiting announcement
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
