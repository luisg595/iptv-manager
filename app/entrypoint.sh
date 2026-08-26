#!/bin/sh
set -eu

if [ "${UPDATE_YTDLP_ON_START:-true}" = "true" ]; then
  echo "[inicio] comprobando actualización de yt-dlp"
  python -m pip install --no-cache-dir --upgrade yt-dlp || \
    echo "[inicio] no se pudo actualizar yt-dlp; se usará la versión instalada"
fi

exec gunicorn \
  --bind 0.0.0.0:8090 \
  --workers 1 \
  --threads 24 \
  --timeout 180 \
  --access-logfile - \
  --error-logfile - \
  manager:app
