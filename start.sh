#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
cd "$ROOT"
PYTHON=/Users/aeon/aeon-env/bin/python
UVICORN=/Users/aeon/aeon-env/bin/uvicorn
PID_FILE="$ROOT/data/server.pid"
mkdir -p "$ROOT/data"

if [ -s "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    case "$PID" in *[!0-9]*|'') PID=0 ;; esac
    if [ "$PID" -gt 0 ] && kill -0 "$PID" 2>/dev/null; then
        COMMAND=$(ps -p "$PID" -o command= 2>/dev/null || true)
        case "$COMMAND" in
            *uvicorn*"--app-dir $ROOT --host 0.0.0.0 --port 8787"*)
                echo "Splice is already running (PID $PID): http://127.0.0.1:8787"
                exit 0 ;;
            *) echo "The PID file refers to another process. Leaving it alone." >&2; exit 1 ;;
        esac
    fi
fi

for TOOL in "$PYTHON" "$UVICORN" /opt/homebrew/bin/ffmpeg /opt/homebrew/bin/ffprobe /Users/aeon/aeon-env/bin/yt-dlp; do
    if [ ! -x "$TOOL" ]; then
        echo "Required tool not found: $TOOL. See README.md; this script installs nothing." >&2
        exit 1
    fi
done

"$PYTHON" - <<'PY'
import socket
try:
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("0.0.0.0", 8787))
except OSError as error:
    raise SystemExit(f"Cannot bind 0.0.0.0:8787: {error}. Check port availability and local socket permissions.")
PY

nohup "$UVICORN" app:app --app-dir "$ROOT" --host 0.0.0.0 --port 8787 > "$ROOT/data/server.log" 2>&1 < /dev/null &
PID=$!
printf '%s\n' "$PID" > "$PID_FILE"
ATTEMPT=0
while [ "$ATTEMPT" -lt 40 ]; do
    if ! kill -0 "$PID" 2>/dev/null; then
        echo "Splice could not start. Check data/server.log." >&2
        exit 1
    fi
    if curl --noproxy '*' --fail --silent --max-time 1 http://127.0.0.1:8787/api/health > /dev/null; then
        echo "Splice started (PID $PID)."
        echo "Local: http://127.0.0.1:8787"
        echo "LAN:   http://192.168.1.98:8787"
        echo "Log:   data/server.log"
        exit 0
    fi
    ATTEMPT=$((ATTEMPT + 1))
    sleep 0.25
done
kill "$PID" 2>/dev/null || true
echo "The server did not become ready. Check data/server.log." >&2
exit 1
