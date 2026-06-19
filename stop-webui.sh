#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -f webui.pid ]; then
    echo "Web UI is not running (no PID file found)"
    exit 1
fi

PID=$(cat webui.pid)

if ! ps -p "$PID" > /dev/null 2>&1; then
    echo "Web UI is not running (stale PID file)"
    rm -f webui.pid
    exit 1
fi

echo "Stopping Web UI (PID: $PID)..."
kill -TERM "$PID" 2>/dev/null || true

for i in {1..10}; do
    if ! ps -p "$PID" > /dev/null 2>&1; then
        echo "Web UI stopped"
        rm -f webui.pid
        exit 0
    fi
    sleep 1
done

echo "Force killing Web UI..."
kill -KILL "$PID" 2>/dev/null || true
rm -f webui.pid

echo "Web UI stopped"
