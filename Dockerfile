FROM ghcr.io/xinnan-tech/xiaozhi-esp32-server:server-base

USER root

RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        nginx \
        ffmpeg \
        curl \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -U yt-dlp httpx

WORKDIR /opt/xiaozhi-esp32-server

RUN git clone --depth 1 https://github.com/xinnan-tech/xiaozhi-esp32-server.git /tmp/xz \
    && cp -r /tmp/xz/main/xiaozhi-server/. /opt/xiaozhi-esp32-server/ \
    && rm -rf /tmp/xz \
    && mkdir -p /opt/xiaozhi-esp32-server/data /opt/xiaozhi-esp32-server/music /opt/xiaozhi-esp32-server/tmp

RUN if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt || true; fi

COPY play_music.py /opt/xiaozhi-esp32-server/plugins_func/functions/play_music.py
COPY tts_edge.py /opt/xiaozhi-esp32-server/core/providers/tts/edge.py
COPY asr_openai.py /opt/xiaozhi-esp32-server/core/providers/asr/openai.py
COPY config.template.yaml /opt/xiaozhi-esp32-server/config.template.yaml
COPY nginx.conf /etc/nginx/nginx.conf.template
COPY start.sh /opt/xiaozhi-esp32-server/start.sh

RUN sed -i 's/\r$//' /opt/xiaozhi-esp32-server/start.sh \
    && chmod +x /opt/xiaozhi-esp32-server/start.sh \
    && chmod -R 777 /opt/xiaozhi-esp32-server/data /opt/xiaozhi-esp32-server/music /opt/xiaozhi-esp32-server/tmp \
    && mkdir -p /var/log/nginx /var/lib/nginx /run \
    && chmod -R 777 /var/log/nginx /var/lib/nginx /run

EXPOSE 10000 7860

CMD ["/opt/xiaozhi-esp32-server/start.sh"]
