#!/bin/bash
set -e

# ARTE Recorder - Linux/Proxmox Setup Script
# Run this on a fresh Debian/Ubuntu/Proxmox container or VM

echo "=== ARTE Recorder - Linux Setup ==="

# Update packages
sudo apt-get update

# Install dependencies: Python, ffmpeg, curl
sudo apt-get install -y \
    python3 \
    python3-venv \
    python3-pip \
    ffmpeg \
    curl \
    git \
    wget

cd "$(dirname "$0")"

# Create virtual environment
if [ ! -d "venv" ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv venv
fi

# Install Python dependencies
echo "Installing Python packages..."
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Create directories
mkdir -p recordings metadata temp

echo ""
echo "=== Setup complete ==="
echo ""
echo "Start the web UI:"
echo "  ./start-webui.sh"
echo ""
echo "Or run directly:"
echo "  source venv/bin/activate"
echo "  python3 webui.py --host 0.0.0.0 --port 5050"
echo ""
echo "Access the web UI at:"
echo "  http://$(hostname -I | awk '{print $1}'):5050"
