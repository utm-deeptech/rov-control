#!/usr/bin/env bash
# Bench session over the ST-LINK (no UART wires, no rov-bridge service): run on the Pi.
#   bash pi/bench_start.sh [service ...]   # services to pause first, e.g. one that holds the camera
# Nothing persists: a reboot or pi/bench_stop.sh undoes it.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PI_IP="$(python3 config/cfg.py network.pi_ip)"
echo "$*" > /tmp/rov_bench_paused

for svc in "$@"; do sudo systemctl stop "$svc" && echo "paused $svc"; done
sudo ip addr add "$PI_IP/24" dev eth0 2>/dev/null && echo "eth0 += $PI_IP/24 (until reboot)"

pkill -INT -f "python3 pi/swd_bridge"; sleep 1.5
sudo pkill openocd                           # a bridge killed without cleanup leaves openocd holding the ST-LINK
sleep 0.5
nohup python3 pi/swd_bridge.py > /tmp/swd_bridge.log 2>&1 < /dev/null &
sleep 4
tail -1 /tmp/swd_bridge.log
