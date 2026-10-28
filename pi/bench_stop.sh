#!/usr/bin/env bash
# Ends a pi/bench_start.sh session: bridge, camera, eth0 address, and restarts the services it paused.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PI_IP="$(python3 "$ROOT/config/cfg.py" network.pi_ip)"

pkill -INT -f "python3 pi/swd_bridge"; sleep 1.5      # SIGINT: the bridge stops the camera and openocd itself
sudo pkill openocd; pkill rpicam-vid
sudo ip addr del "$PI_IP/24" dev eth0 2>/dev/null && echo "eth0 -= $PI_IP/24"
for svc in $(cat /tmp/rov_bench_paused 2>/dev/null); do sudo systemctl start "$svc" && echo "restarted $svc"; done
rm -f /tmp/rov_bench_paused
echo "bench session stopped"
