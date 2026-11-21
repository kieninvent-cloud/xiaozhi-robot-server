import os
import re
import uuid
import urllib.parse
from datetime import datetime

import edge_tts
import httpx
from core.providers.tts.base import TTSProviderBase


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
            self.voice = config.get("voice")
        self.audio_file_type = config.get("format", "mp3")

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
        self._http = httpx.AsyncClient(
            timeout=6.0,
            headers={"User-Agent": "Mozilla/5.0"},
        )

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

    async def _google_tts_bytes(self, text: str) -> bytes:
        chunks = self._split_chunks(text, max_len=180)
        if not chunks:
            return b""
        audio = bytearray()
        for chunk in chunks:
            q = urllib.parse.quote(chunk)
            url = f"https://translate.google.com/translate_tts?ie=UTF-8&tl=vi&client=tw-ob&q={q}"
            resp = await self._http.get(url)
            if resp.status_code == 200 and resp.content:
                audio.extend(resp.content)
            else:
                raise RuntimeError(f"Google TTS HTTP {resp.status_code}")
        return bytes(audio)

    async def text_to_speak(self, text, output_file):
        # Fast path: Google Vietnamese TTS (~0.3-0.8s, no EdgeTTS throttling)
        try:
            audio_bytes = await self._google_tts_bytes(text)
            if audio_bytes:
                if output_file:
                    os.makedirs(os.path.dirname(output_file), exist_ok=True)
                    with open(output_file, "wb") as f:
                        f.write(audio_bytes)
                    return None
                return audio_bytes
        except Exception:
            pass

        # Fallback: EdgeTTS
        try:
            communicate = edge_tts.Communicate(
                text,
                voice=self.voice,
                rate=self.edge_rate,
                volume=self.edge_volume,
                pitch=self.edge_pitch,
            )
            if output_file:
                os.makedirs(os.path.dirname(output_file), exist_ok=True)
                with open(output_file, "wb") as f:
                    async for chunk in communicate.stream():
                        if chunk["type"] == "audio":
                            f.write(chunk["data"])
            else:
                audio_bytes = b""
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        audio_bytes += chunk["data"]
                return audio_bytes
        except Exception as e:
            raise Exception(f"TTS请求失败: {e}")
