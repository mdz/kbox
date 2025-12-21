#!/bin/bash
# Deploy kbox to Raspberry Pi
#
# Usage: ./deploy.sh <pi-ip-or-hostname>
# Example: ./deploy.sh 192.168.1.100
# Example: ./deploy.sh kbox.local

set -e

PI_HOST="${1:-}"
PI_USER="${PI_USER:-pi}"
PI_PATH="${PI_PATH:-/home/pi/kbox}"

if [ -z "$PI_HOST" ]; then
    echo "Usage: $0 <pi-ip-or-hostname>"
    echo ""
    echo "Environment variables:"
    echo "  PI_USER  - SSH user (default: pi)"
    echo "  PI_PATH  - Path on Pi (default: /home/pi/kbox)"
    echo ""
    echo "Examples:"
    echo "  $0 192.168.1.100"
    echo "  $0 kbox.local"
    echo "  PI_USER=admin PI_PATH=/opt/kbox $0 mypi.local"
    exit 1
fi

echo "=== Deploying kbox to ${PI_USER}@${PI_HOST}:${PI_PATH} ==="

# Sync code to Pi
echo "Syncing code..."
rsync -avz --delete \
    --exclude '.git' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude '.venv' \
    --exclude 'data/' \
    --exclude '.cursor' \
    "$(dirname "$0")/" "${PI_USER}@${PI_HOST}:${PI_PATH}/"

echo ""
echo "=== Code synced! ==="
echo ""
echo "Next steps on the Pi (ssh ${PI_USER}@${PI_HOST}):"
echo ""
echo "Option A - Docker:"
echo "  cd ${PI_PATH}"
echo "  docker-compose build"
echo "  docker-compose up -d"
echo ""
echo "Option B - Native (first time setup):"
echo "  sudo apt update"
echo "  sudo apt install python3-gst-1.0 gstreamer1.0-alsa python3-mido python3-rtmidi \\"
echo "      rubberband-ladspa gstreamer1.0-plugins-bad gstreamer1.0-plugins-good \\"
echo "      python3-pip python3-venv ffmpeg"
echo "  cd ${PI_PATH}"
echo "  python3 -m venv .venv --system-site-packages"
echo "  source .venv/bin/activate"
echo "  pip install fastapi uvicorn jinja2 itsdangerous google-api-python-client yt-dlp"
echo "  python -m kbox.main"
echo ""
echo "Option B - Native (subsequent deploys):"
echo "  cd ${PI_PATH}"
echo "  source .venv/bin/activate"
echo "  pkill -f 'kbox.main' || true"
echo "  python -m kbox.main"



