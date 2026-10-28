#!/usr/bin/env bash
# Camera -> H.264 RTP/UDP -> laptop. Run by rov-camera.service (or by hand).
# Everything comes from config/system.yaml.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cfg() { python3 "$ROOT/config/cfg.py" "$1"; }

HOST="${ROV_LAPTOP_IP:-$(cfg network.laptop_ip)}"
PORT="$(cfg network.video_port)"
W="$(cfg video.width)"; H="$(cfg video.height)"; FPS="$(cfg video.fps)"
BR="$(cfg video.bitrate)"; INTRA="$(cfg video.intra)"
CAM="$(cfg pi.camera)"; DEV="$(cfg pi.usb_device)"
KBPS=$((BR / 1000))

SINK="rtph264pay config-interval=1 pt=96 ! udpsink host=$HOST port=$PORT sync=false"
echo "camera: $CAM ${W}x${H}@${FPS} ${KBPS}kbps -> $HOST:$PORT"

if [[ "$CAM" == "picam" ]]; then
  VID=""
  command -v rpicam-vid >/dev/null && VID=rpicam-vid
  [[ -z "$VID" ]] && command -v libcamera-vid >/dev/null && VID=libcamera-vid
  if [[ -n "$VID" ]]; then
    # shellcheck disable=SC2086
    exec bash -c "$VID -t 0 -n --inline --width $W --height $H --framerate $FPS --bitrate $BR \
      --intra $INTRA --codec h264 --low-latency --flush --denoise cdn_off -o - | \
      gst-launch-1.0 -q fdsrc ! h264parse ! $SINK"
  fi
  if gst-inspect-1.0 libcamerasrc >/dev/null 2>&1; then
    # Ubuntu Server path (no rpicam-apps). Hardware encoder on Pi 4, x264 elsewhere.
    if gst-inspect-1.0 v4l2h264enc >/dev/null 2>&1; then
      ENC="v4l2h264enc extra-controls=\"controls,video_bitrate=$BR,h264_i_frame_period=$INTRA,repeat_sequence_header=1\" ! video/x-h264,level=(string)4"
    else
      ENC="x264enc tune=zerolatency speed-preset=ultrafast bitrate=$KBPS key-int-max=$INTRA"
    fi
    exec bash -c "gst-launch-1.0 -q libcamerasrc ! video/x-raw,width=$W,height=$H,framerate=$FPS/1,format=NV12 ! \
      videoconvert ! $ENC ! h264parse ! $SINK"
  fi
  echo "ERROR: no rpicam-vid/libcamera-vid and no GStreamer libcamerasrc on this OS." >&2
  echo "The Pi Camera is best supported on Raspberry Pi OS Lite (64-bit). Reflash with it, or set pi.camera: usb." >&2
  exit 1
fi

# USB webcam
if gst-inspect-1.0 v4l2h264enc >/dev/null 2>&1; then
  ENC="videoconvert ! video/x-raw,format=I420 ! v4l2h264enc extra-controls=\"controls,video_bitrate=$BR,h264_i_frame_period=$INTRA,repeat_sequence_header=1\" ! video/x-h264,level=(string)4"
else
  ENC="videoconvert ! x264enc tune=zerolatency speed-preset=ultrafast bitrate=$KBPS key-int-max=$INTRA"
fi
exec bash -c "gst-launch-1.0 -q v4l2src device=$DEV ! video/x-raw,width=$W,height=$H,framerate=$FPS/1 ! \
  $ENC ! h264parse ! $SINK"
