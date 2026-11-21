import asyncio
import io
import os
import time
from typing import List, Optional, Tuple

import requests
import speech_recognition as sr
from pydub import AudioSegment, effects

from config.logger import setup_logging
from core.providers.asr.base import ASRProviderBase
from core.providers.asr.dto.dto import InterfaceType

TAG = __name__
logger = setup_logging()


class ASRProvider(ASRProviderBase):
    def __init__(self, config: dict, delete_audio_file: bool):
        self.interface_type = InterfaceType.NON_STREAM
        self.api_key = config.get("api_key")
        self.api_url = config.get("base_url")
        self.model = config.get("model_name")
        self.output_dir = config.get("output_dir")
        self.delete_audio_file = delete_audio_file
        self.language = config.get("language", "vi")
        self._session = requests.Session()
        self._recognizer = sr.Recognizer()

        os.makedirs(self.output_dir, exist_ok=True)

    def requires_file(self) -> bool:
        return True

    def _prepare_normalized_wav(self, file_path: str) -> bytes:
        """Boost quiet INMP441 mic audio to optimal loudness for Google/Whisper ASR."""
        seg = AudioSegment.from_file(file_path)
        seg = seg.set_frame_rate(16000).set_channels(1).set_sample_width(2)
        if seg.dBFS > -60:
            seg = effects.normalize(seg, headroom=2.0)
        buf = io.BytesIO()
        seg.export(buf, format="wav")
        return buf.getvalue()

    def _recognize_google_vi(self, wav_bytes: bytes) -> str:
        with sr.AudioFile(io.BytesIO(wav_bytes)) as source:
            audio = self._recognizer.record(source)
        return self._recognizer.recognize_google(audio, language="vi-VN")

    def _recognize_groq_whisper(self, wav_bytes: bytes) -> str:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        data = {
            "model": self.model,
            "language": self.language,
            "temperature": "0",
            "prompt": "Tiếng Việt chuẩn có dấu. Mở bài hát, bật đèn, tắt đèn, tiến lên phía trước, lùi lại đằng sau, xoay theo chiều kim đồng hồ, xoay ngược chiều kim đồng hồ, dừng lại, tăng âm lượng, giảm âm lượng.",
        }
        files = {"file": ("audio.wav", wav_bytes, "audio/wav")}
        resp = self._session.post(
            self.api_url, files=files, data=data, headers=headers, timeout=10
        )
        if resp.status_code == 200:
            return resp.json().get("text", "").strip()
        raise RuntimeError(f"Groq ASR HTTP {resp.status_code}: {resp.text[:200]}")

    async def speech_to_text(
        self, opus_data: List[bytes], session_id: str, artifacts=None
    ) -> Tuple[Optional[str], Optional[str]]:
        if artifacts is None:
            return "", None
        file_path = artifacts.file_path
        logger.bind(tag=TAG).info(f"file path: {file_path}")

        try:
            start_time = time.time()
            wav_bytes = await asyncio.to_thread(self._prepare_normalized_wav, file_path)

            # 1. Primary: Google Speech Recognition (vi-VN) - highest Vietnamese accuracy
            try:
                text = await asyncio.to_thread(self._recognize_google_vi, wav_bytes)
                if text and text.strip():
                    logger.bind(tag=TAG).info(
                        f"Google ASR ({time.time() - start_time:.2f}s): {text}"
                    )
                    return text.strip(), file_path
            except sr.UnknownValueError:
                logger.bind(tag=TAG).warning("Google ASR không nghe rõ, thử Groq Whisper...")
            except Exception as ge:
                logger.bind(tag=TAG).warning(f"Google ASR lỗi ({ge}), chuyển sang Groq Whisper...")

            # 2. Fallback: Groq Whisper large-v3
            text = await asyncio.to_thread(self._recognize_groq_whisper, wav_bytes)
            logger.bind(tag=TAG).info(
                f"Groq Whisper ASR ({time.time() - start_time:.2f}s): {text}"
            )
            return text, file_path

        except Exception as e:
            logger.bind(tag=TAG).error(f"语音识别失败: {e}")
            return "", None
