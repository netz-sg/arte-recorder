#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ -f recorder.pid ]; then
    PID=$(cat recorder.pid)
    if ps -p "$PID" > /dev/null 2>&1; then
        echo "Recorder is already running (PID: $PID)"
        exit 1
    else
        rm -f recorder.pid
    fi
fi

source venv/bin/activate

# Create necessary directories
mkdir -p recordings metadata temp

# Start the recorder in background
nohup python3 recorder.py config.json > recorder.log 2>&1 &

sleep 1

if [ -f recorder.pid ]; then
    PID=$(cat recorder.pid)
    echo "Recorder started (PID: $PID)"
    echo "Logs: $SCRIPT_DIR/recorder.log"
    echo "Recordings: $SCRIPT_DIR/recordings"
    echo "Metadata: $SCRIPT_DIR/metadata"
else
    echo "Failed to start recorder. Check recorder.log"
    exit 1
fi
