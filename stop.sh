#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -f recorder.pid ]; then
    echo "Recorder is not running (no PID file found)"
    exit 1
fi

PID=$(cat recorder.pid)

if ! ps -p "$PID" > /dev/null 2>&1; then
    echo "Recorder is not running (stale PID file)"
    rm -f recorder.pid
    exit 1
fi

echo "Stopping recorder (PID: $PID)..."
kill -TERM "$PID" 2>/dev/null || true

# Wait up to 15 seconds for graceful shutdown
for i in {1..15}; do
    if ! ps -p "$PID" > /dev/null 2>&1; then
        echo "Recorder stopped"
        rm -f recorder.pid
        exit 0
    fi
    sleep 1
done

echo "Force killing recorder..."
kill -KILL "$PID" 2>/dev/null || true
rm -f recorder.pid

echo "Recorder stopped"
