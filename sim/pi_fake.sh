#!/usr/bin/env bash
# Run ON THE PI (via `make pi-fake`): swap the real UART for the fake Blackpill, so the
# laptop app can be tested over the real tether before the firmware exists. Ctrl+C restores.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
sudo systemctl stop rov-bridge
python3 "$ROOT/sim/ptypair.py" >/tmp/rov_pty.txt & PP=$!
sleep 0.5; read -r A B </tmp/rov_pty.txt
python3 "$ROOT/pi/bridge.py" --serial "$B" >/tmp/rov_bridge_fake.log 2>&1 & BR=$!
cleanup() { kill $BR $PP 2>/dev/null; sudo systemctl start rov-bridge; echo; echo "real bridge restored"; }
trap cleanup EXIT
python3 "$ROOT/sim/fake_blackpill.py" --serial "$A"
