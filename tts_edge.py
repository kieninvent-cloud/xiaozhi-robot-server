import asyncio
import json
import os
import re
import threading
import uuid
import urllib.parse
from datetime import datetime

import edge_tts
import httpx
from core.providers.tts.base import TTSProviderBase

SETTINGS_FILE = os.path.abspath("./data/web_settings.json")
_THREAD_LOCAL = threading.local()


def _get_thread_http_client() -> httpx.Client:
    client = getattr(_THREAD_LOCAL, "http_client", None)
    if client is None or client.is_closed:
        client = httpx.Client(
            timeout=10.0,
            headers={"User-Agent": "Mozilla/5.0"},
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20, keepalive_expiry=60.0),
        )
        _THREAD_LOCAL.http_client = client
    return client


def _fetch_google_chunk_sync(chunk: str, speed: float = 1.0) -> bytes:
    q = urllib.parse.quote(chunk)
    ttsspeed = max(0.5, min(1.5, speed))
    url = f"https://translate.google.com/translate_tts?ie=UTF-8&tl=vi&client=tw-ob&ttsspeed={ttsspeed}&q={q}"
    client = _get_thread_http_client()
    for _ in range(2):
        resp = client.get(url)
        if resp.status_code == 200 and resp.content:
            return resp.content
    raise RuntimeError(f"Google TTS HTTP {resp.status_code}")


def _load_live_tts_settings():
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


class TTSProvider(TTSProviderBase):
    TTS_PARAM_CONFIG = [
        ("ttsVolume", "volume", 0, 100, 50, int),
        ("ttsRate", "speech_rate", -100, 100, 0, int),
        ("ttsPitch", "pitch_rate", -100, 100, 0, int),
    ]

    def __init__(self, config, delete_audio_file):
        super().__init__(config, delete_audio_file)
        if config.get("private_voice"):
            self.voice = config.get("private_voice")
        else:
            self.voice = config.get("voice", "vi-VN-HoaiMyNeural")
        self.audio_file_type = config.get("format", "mp3")
        self.tts_engine = config.get("tts_engine", "google")

        volume = config.get("volume", "0")
        self.volume = int(volume) if volume else 0

        speech_rate = config.get("rate", "0")
        self.speech_rate = int(speech_rate) if speech_rate else 0

        pitch_rate = config.get("pitch", "0")
        self.pitch_rate = int(pitch_rate) if pitch_rate else 0

        self._apply_percentage_params(config)

        self.edge_rate = f"{self.speech_rate:+}%"
        self.edge_volume = f"{self.volume:+}%"
        self.edge_pitch = f"{self.pitch_rate:+}Hz"

    def generate_filename(self, extension=".mp3"):
        return os.path.join(
            self.output_file,
            f"tts-{datetime.now().date()}@{uuid.uuid4().hex}{extension}",
        )

    @staticmethod
    def _split_chunks(text: str, max_len: int = 180):
        clean = re.sub(r"\s+", " ", (text or "").strip())
        if not clean:
            return []
        if len(clean) <= max_len:
            return [clean]
        parts = re.split(r"(?<=[,.!?;:])\s+", clean)
        chunks, cur = [], ""
        for p in parts:
            if len(cur) + len(p) + 1 <= max_len:
                cur = f"{cur} {p}".strip()
            else:
                if cur:
                    chunks.append(cur)
                while len(p) > max_len:
                    chunks.append(p[:max_len])
                    p = p[max_len:].strip()
                cur = p
        if cur:
            chunks.append(cur)
        return chunks

    async def _google_tts_bytes(self, text: str, rate_pct: int = 0) -> bytes:
        chunks = self._split_chunks(text, max_len=180)
        if not chunks:
            return b""
        speed = 1.0 + (rate_pct / 100.0)
        results = [_fetch_google_chunk_sync(c, speed) for c in chunks]
        return b"".join(results)

    async def _edge_tts_bytes(self, text: str, voice: str, rate_pct: int, pitch_hz: int) -> bytes:
        rate_str = f"{rate_pct:+}%"
        pitch_str = f"{pitch_hz:+}Hz"
        last_err = None
        for _ in range(2):
            try:
                communicate = edge_tts.Communicate(
                    text,
                    voice=voice,
                    rate=rate_str,
                    volume="+0%",
                    pitch=pitch_str,
                )
                audio_bytes = bytearray()
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        audio_bytes.extend(chunk["data"])
                if audio_bytes:
                    return bytes(audio_bytes)
            except Exception as e:
                last_err = e
                await asyncio.sleep(0.3)
        raise RuntimeError(f"Edge TTS failed: {last_err}")

    async def text_to_speak(self, text, output_file):
        live = _load_live_tts_settings()
        engine = live.get("tts_engine", self.tts_engine)
        rate_pct = int(live.get("tts_rate", self.speech_rate) or 0)
        pitch_hz = int(live.get("tts_pitch", self.pitch_rate) or 0)

        # Locked single voice per user choice (no random voice switching!)
        if engine == "edge_female":
            audio_bytes = await self._edge_tts_bytes(text, "vi-VN-HoaiMyNeural", rate_pct, pitch_hz)
        elif engine == "edge_male":
            audio_bytes = await self._edge_tts_bytes(text, "vi-VN-NamMinhNeural", rate_pct, pitch_hz)
        else:
            # Default: Google Vietnamese TTS (fast, consistent, parallel chunks)
            audio_bytes = await self._google_tts_bytes(text, rate_pct)

        if output_file:
            os.makedirs(os.path.dirname(output_file), exist_ok=True)
            with open(output_file, "wb") as f:
                f.write(audio_bytes)
            return None
        return audio_bytes
