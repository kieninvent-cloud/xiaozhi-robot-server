---
title: My XiaoZhi Robot
emoji: 🤖
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
---

# XiaoZhi ESP32 Server (Groq AI + EdgeTTS Vietnamese + YouTube Music)

Server riêng cho Robot XiaoZhi ESP32-S3:
- **LLM**: Groq `llama-3.3-70b-versatile` (thông minh, phản hồi siêu nhanh, hỗ trợ gọi công cụ điều khiển động cơ & nhạc)
- **ASR**: Groq Whisper `whisper-large-v3-turbo` (nhận diện giọng nói Tiếng Việt chuẩn xác)
- **TTS**: EdgeTTS `vi-VN-HoaiMyNeural` (giọng Tiếng Việt tự nhiên, miễn phí)
- **Nhạc YouTube**: Tìm & phát bất kỳ bài hát nào trên YouTube thẳng ra loa ESP32 bằng `yt-dlp`
