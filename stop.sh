#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
PID_FILE="$ROOT/data/server.pid"
if [ ! -s "$PID_FILE" ]; then
    echo "Splice is not running via start.sh."
    exit 0
fi
PID=$(cat "$PID_FILE")
case "$PID" in *[!0-9]*|'') echo "Invalid PID file; no process was stopped." >&2; exit 1 ;; esac
if [ "$PID" -le 0 ]; then
    echo "Invalid PID file; no process was stopped." >&2
    exit 1
fi
if ! kill -0 "$PID" 2>/dev/null; then
    : > "$PID_FILE"
    echo "Splice is already stopped."
    exit 0
fi
COMMAND=$(ps -p "$PID" -o command= 2>/dev/null || true)
case "$COMMAND" in
    *uvicorn*"--app-dir $ROOT --host 127.0.0.1 --port 8787"*) ;;
    *) echo "The PID file refers to another process. Leaving it alone." >&2; exit 1 ;;
esac
kill -TERM "$PID"
ATTEMPT=0
while kill -0 "$PID" 2>/dev/null && [ "$ATTEMPT" -lt 40 ]; do
    STATE=$(ps -p "$PID" -o stat= 2>/dev/null || true)
    case "$STATE" in *Z*) break ;; esac
    ATTEMPT=$((ATTEMPT + 1))
    sleep 0.25
done
if kill -0 "$PID" 2>/dev/null; then
    STATE=$(ps -p "$PID" -o stat= 2>/dev/null || true)
    case "$STATE" in
        *Z*) ;;
        *) echo "Shutdown requested; an active local job is still finishing. Run stop.sh again shortly."; exit 1 ;;
    esac
fi
: > "$PID_FILE"
echo "Splice stopped."
