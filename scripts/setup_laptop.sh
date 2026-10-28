#!/usr/bin/env bash
# Phase 1: laptop packages + Python venv. Run once on the Ubuntu laptop.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
sudo apt update
sudo apt install -y python3-venv python3-gi python3-gi-cairo gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 \
  gstreamer1.0-tools gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
  gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly gstreamer1.0-libav libsdl2-2.0-0 \
  libxcb-cursor0 socat openssh-client git make gcc-arm-none-eabi stlink-tools dfu-util
python3 -m venv --system-site-packages "$ROOT/.venv"
"$ROOT/.venv/bin/pip" install --upgrade pip
"$ROOT/.venv/bin/pip" install --ignore-installed -r "$ROOT/requirements.txt"
chmod +x "$ROOT"/scripts/*.sh "$ROOT"/sim/*.sh "$ROOT"/pi/*.sh
"$ROOT/.venv/bin/python" -c "import gi; gi.require_version('Gst','1.0'); from gi.repository import Gst; Gst.init(None); print('GStreamer', Gst.version_string())"
echo "Laptop ready. Try: make test && make run-sim"
