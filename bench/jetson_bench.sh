#!/usr/bin/env bash
# Vehicle-side bench on the Jetson (stand-in for the ROV's Pi). Run from the repo root on the Jetson.
#
#   bash bench/jetson_bench.sh start [swd|uart]   # swd (default): Black Pill over the ST-LINK, no UART wires needed
#                                                 # uart: the real link, /dev/ttyTHS1 (Jetson pin 8 -> PA10, pin 10 <- PA9)
#   bash bench/jetson_bench.sh flash [sim|real]   # build + flash (sim = fake depth/IMU plant for the PID holds)
#   bash bench/jetson_bench.sh stop | status | logs
set -u
cd "$(dirname "$0")/.."
LOGS=logs
mkdir -p "$LOGS"
ELF_sim=firmware/build/blackpill-sim/rovfw.elf
ELF_real=firmware/build/blackpill/rovfw.elf

stop() {
    pkill -f "[b]ench/swd_link.py" && echo "stopped swd_link"
    pkill -f "[p]i/bridge.py" && echo "stopped bridge"
    pkill -x openocd 2>/dev/null
    sleep 0.5
    return 0
}

case "${1:-status}" in
flash)
    stop
    if [ "${2:-sim}" = sim ]; then make -s -C firmware SIM=1 && make -s -C firmware SIM=1 flash 2>&1 | grep -E "Verified|rror"
    else make -s -C firmware && make -s -C firmware flash 2>&1 | grep -E "Verified|rror"; fi
    echo "${2:-sim}" > "$LOGS/flashed"
    ;;
start)
    stop
    how=${2:-swd}
    if [ "$how" = swd ]; then
        elf=$ELF_sim; [ "$(cat "$LOGS/flashed" 2>/dev/null)" = real ] && elf=$ELF_real
        setsid nohup python3 bench/swd_link.py --elf "$elf" > "$LOGS/swd_link.log" 2>&1 < /dev/null &
        for _ in $(seq 30); do [ -e /tmp/rov-swd ] && break; sleep 0.2; done
        dev=/tmp/rov-swd
    else
        dev=/dev/ttyTHS1
    fi
    setsid nohup python3 pi/bridge.py --serial "$dev" --no-camera > "$LOGS/bridge.log" 2>&1 < /dev/null &
    sleep 2
    echo "bridge on $dev, UDP :5005 -> telemetry back to whoever sends. Logs: $(pwd)/$LOGS"
    tail -n 3 "$LOGS"/*.log
    ;;
stop) stop ;;
status) pgrep -af "[s]wd_link.py|[p]i/bridge.py|openocd" || echo "nothing running" ;;
logs) tail -n 20 "$LOGS"/*.log ;;
*) echo "usage: $0 start [swd|uart] | flash [sim|real] | stop | status | logs"; exit 1 ;;
esac
