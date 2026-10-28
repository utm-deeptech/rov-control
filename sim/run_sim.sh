#!/usr/bin/env bash
# Whole system on the laptop, no hardware:
#   pty pair <-> fake Blackpill  |  bridge.py  |  test video -> :5600  |  app --sim
# The fake Blackpill's live status stays in THIS terminal. Ctrl+C stops everything.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"; [[ -x "$PY" ]] || PY=python3
cfg() { "$PY" "$ROOT/config/cfg.py" "$1"; }
LOGS="$ROOT/sim/logs"; mkdir -p "$LOGS"
VPORT="$(cfg network.video_port)"; SIMLAPTOP="$(cfg sim.laptop_ip)"
W="$(cfg video.width)"; H="$(cfg video.height)"; FPS="$(cfg video.fps)"
KBPS=$(( $(cfg video.bitrate) / 1000 )); INTRA="$(cfg video.intra)"

PIDS=()
cleanup() {
  trap - EXIT INT TERM
  echo; echo "[sim] stopping..."
  for p in "${PIDS[@]}"; do kill "$p" 2>/dev/null; done
  wait 2>/dev/null
}
trap cleanup EXIT INT TERM

# 1. pty pair
if command -v socat >/dev/null; then
  socat -d -d pty,raw,echo=0 pty,raw,echo=0 2>"$LOGS/socat.log" & PIDS+=($!)
  for _ in $(seq 50); do [[ $(grep -c 'PTY is' "$LOGS/socat.log") -ge 2 ]] && break; sleep 0.1; done
  mapfile -t PTYS < <(grep -o '/dev/pts/[0-9]*' "$LOGS/socat.log")
else
  "$PY" "$ROOT/sim/ptypair.py" >"$LOGS/ptypair.txt" & PIDS+=($!)
  sleep 0.5; read -r -a PTYS <"$LOGS/ptypair.txt"
fi
[[ ${#PTYS[@]} -ge 2 ]] || { echo "[sim] could not create pty pair"; exit 1; }
echo "[sim] pty pair: Blackpill=${PTYS[0]}  bridge=${PTYS[1]}"

# 2. bridge (the 'Pi')
"$PY" "$ROOT/pi/bridge.py" --no-camera --serial "${PTYS[1]}" --laptop "$SIMLAPTOP" >"$LOGS/bridge.log" 2>&1 & PIDS+=($!)

# 3. test video (moving ball + running clock)
ENC=""
for e in "x264enc tune=zerolatency speed-preset=ultrafast bitrate=$KBPS key-int-max=$INTRA" \
         "openh264enc bitrate=$((KBPS * 1000)) gop-size=$INTRA"; do
  gst-inspect-1.0 "${e%% *}" >/dev/null 2>&1 && { ENC="$e"; break; }
done
if [[ -n "$ENC" ]]; then
  gst-launch-1.0 -q videotestsrc is-live=true pattern=ball ! \
    "video/x-raw,width=$W,height=$H,framerate=$FPS/1" ! timeoverlay font-desc="Sans 36" ! \
    videoconvert ! $ENC ! h264parse ! rtph264pay config-interval=1 pt=96 ! \
    udpsink host="$SIMLAPTOP" port="$VPORT" sync=false >"$LOGS/video.log" 2>&1 & PIDS+=($!)
  echo "[sim] test video -> $SIMLAPTOP:$VPORT (${ENC%% *})"
else
  echo "[sim] no H.264 encoder found (sudo apt install gstreamer1.0-plugins-ugly) — running without video"
fi

# 4. the app
"$PY" "$ROOT/topside/app.py" --sim >"$LOGS/app.log" 2>&1 &
APP=$!; PIDS+=($APP)
echo "[sim] app started (log: sim/logs/app.log). Closing the app or Ctrl+C here ends the sim."
echo "[sim] failsafe test: arm in the app, then 'kill -STOP $APP' in another terminal (freezes the app) — watch FAILSAFE below, then 'kill -CONT $APP'."
echo

# 5. fake Blackpill in the foreground (live status line); stop when the app exits
"$PY" "$ROOT/sim/fake_blackpill.py" --serial "${PTYS[0]}" & BP=$!; PIDS+=($BP)
while kill -0 "$APP" 2>/dev/null && kill -0 "$BP" 2>/dev/null; do sleep 0.3; done
sleep 1   # let the failsafe message print if the app was killed while armed
