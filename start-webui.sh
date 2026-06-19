#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PORT="${1:-5050}"

if [ -f webui.pid ]; then
    PID=$(cat webui.pid)
    if ps -p "$PID" > /dev/null 2>&1; then
        echo "Web UI is already running (PID: $PID)"
        echo "Open: http://localhost:$PORT"
        exit 1
    else
        rm -f webui.pid
    fi
fi

source venv/bin/activate

# Create necessary directories
mkdir -p recordings metadata temp

# Start the web UI in background
nohup python3 webui.py --port "$PORT" > webui.log 2>&1 &
PID=$!
echo $PID > webui.pid

disown

sleep 2

if ps -p "$PID" > /dev/null 2>&1; then
    echo "Web UI started (PID: $PID)"
    echo "Open: http://localhost:$PORT"
    echo "Logs: $SCRIPT_DIR/webui.log"
else
    echo "Failed to start Web UI. Check webui.log"
    exit 1
fi
