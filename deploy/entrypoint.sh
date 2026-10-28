#!/bin/sh
set -eu
mkdir -p /data/models /data/uploads /data/jobs /data/media
if [ ! -f /data/models/base/model.bin ] && [ -d /app/models/base ]; then
  cp -a /app/models/base /data/models/base
fi
nginx -c /etc/nginx/nginx.conf
exec uvicorn app:app --host 127.0.0.1 --port 8000 --workers 1
