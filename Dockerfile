FROM python:3.12-slim-bookworm

RUN apt-get update \
  && apt-get install -y --no-install-recommends ffmpeg nginx \
  && rm -rf /var/lib/apt/lists/* \
  && groupadd --system --gid 10001 splice \
  && useradd --system --uid 10001 --gid splice --home /app --shell /usr/sbin/nologin splice \
  && mkdir -p /data /app/models /var/cache/nginx /var/lib/nginx /var/run \
  && chown -R splice:splice /data /app /var/cache/nginx /var/lib/nginx /var/log/nginx /etc/nginx

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
  && python -c "from huggingface_hub import snapshot_download; snapshot_download(\"Systran/faster-whisper-base\", local_dir=\"/app/models/base\")"

COPY --chown=splice:splice app.py config.py storage.py security.py passwords.py uploads.py pipeline.py transcript.py whisper_worker.py ./
COPY --chown=splice:splice static ./static
COPY deploy/nginx.conf /etc/nginx/nginx.conf
COPY deploy/entrypoint.sh /entrypoint.sh
RUN chmod 755 /entrypoint.sh && chown -R splice:splice /app/models /entrypoint.sh

ENV SPLICE_ENV=production \
    SPLICE_DATA_DIR=/data \
    HOME=/app \
    PYTHONUNBUFFERED=1 \
    HF_HUB_OFFLINE=1

USER splice
EXPOSE 8080
VOLUME ["/data"]
ENTRYPOINT ["/entrypoint.sh"]
