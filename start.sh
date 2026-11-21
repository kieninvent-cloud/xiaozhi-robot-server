#!/bin/bash
set -e

PORT_VAL="${PORT:-7860}"
PUBLIC_HOST_VAL="${RENDER_EXTERNAL_HOSTNAME:-${SPACE_HOST:-${PUBLIC_HOST:-localhost:${PORT_VAL}}}}"
GROQ_KEY_VAL="${GROQ_API_KEY:-}"

if [ -z "$GROQ_KEY_VAL" ]; then
    echo "WARNING: GROQ_API_KEY is not set! Please add GROQ_API_KEY in Environment Variables."
fi

sed -e "s|__PUBLIC_HOST__|${PUBLIC_HOST_VAL}|g" \
    -e "s|__GROQ_API_KEY__|${GROQ_KEY_VAL}|g" \
    /opt/xiaozhi-esp32-server/config.template.yaml > /opt/xiaozhi-esp32-server/data/.config.yaml

sed -e "s|7860|${PORT_VAL}|g" \
    /etc/nginx/nginx.conf.template > /etc/nginx/nginx.conf

echo "Starting XiaoZhi Server on port ${PORT_VAL} for host: ${PUBLIC_HOST_VAL}"

nginx

cd /opt/xiaozhi-esp32-server
exec python app.py
