#!/usr/bin/env bash
# Runs ON THE PI (scripts/deploy_pi.sh calls it over SSH). Idempotent.
#   --no-apt   skip package install (the Pi has no internet on the tether)
#   --no-eth   don't touch eth0 (otherwise it gets the static tether address from config/system.yaml)
set -euo pipefail
ROVDIR="$(cd "$(dirname "$0")/.." && pwd)"
ME="$(id -un)"
DO_APT=1
DO_ETH=1
for a in "$@"; do [[ "$a" == "--no-apt" ]] && DO_APT=0; [[ "$a" == "--no-eth" ]] && DO_ETH=0; done
say() { echo -e "\e[1;36m[install]\e[0m $*"; }
warn() { echo -e "\e[1;33m[install] WARNING:\e[0m $*"; }

. /etc/os-release
case "${ID:-}" in
  raspbian|debian) OS=pios ;;
  ubuntu) OS=ubuntu ;;
  *) OS=unknown; warn "unrecognised OS '${ID:-?}', treating like Raspberry Pi OS" ;;
esac
MODEL="$(tr -d '\0' </proc/device-tree/model 2>/dev/null || echo unknown)"
say "OS=$OS  model=$MODEL  user=$ME  dir=$ROVDIR"

# ---------- 1. packages ----------
if [[ $DO_APT == 1 ]]; then
  say "installing packages (needs internet on the Pi)"
  sudo apt-get update
  sudo apt-get install -y python3-serial python3-yaml gstreamer1.0-tools \
    gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-plugins-bad stlink-tools openocd
  if [[ $OS == pios ]]; then
    sudo apt-get install -y rpicam-apps || sudo apt-get install -y libcamera-apps || warn "rpicam-apps not installed"
  else
    sudo apt-get install -y gstreamer1.0-libcamera gstreamer1.0-plugins-ugly || warn "libcamerasrc not available on this Ubuntu"
  fi
else
  say "skipping apt (--no-apt)"
fi
python3 -c "import serial, yaml" || { echo "python3-serial / python3-yaml missing: give the Pi internet and run without --no-apt"; exit 1; }

# ---------- 2. UART on GPIO14/15, no serial console ----------
BOOT=/boot/firmware; [[ -f $BOOT/config.txt ]] || BOOT=/boot
CONFIG=$BOOT/config.txt; CMDLINE=$BOOT/cmdline.txt
BEFORE="$(cat "$CONFIG" "$CMDLINE" | md5sum)"
if [[ $OS == pios ]] && command -v raspi-config >/dev/null; then
  sudo raspi-config nonint do_serial_hw 0     # enable UART hardware
  sudo raspi-config nonint do_serial_cons 1   # disable login console on it
else
  grep -q '^enable_uart=1' "$CONFIG" || echo 'enable_uart=1' | sudo tee -a "$CONFIG" >/dev/null
  sudo sed -i -E 's/console=(serial0|ttyAMA0|ttyS0),[0-9]+ ?//g' "$CMDLINE"
fi
if [[ "$MODEL" == *"Pi 4"* || "$MODEL" == *"Pi 3"* ]]; then
  # Put the full PL011 UART on GPIO14/15 (mini-UART is unreliable at 460800)
  grep -q '^dtoverlay=disable-bt' "$CONFIG" || echo 'dtoverlay=disable-bt' | sudo tee -a "$CONFIG" >/dev/null
  sudo systemctl disable --now hciuart 2>/dev/null || true
fi
if [[ "$MODEL" == *"Pi 5"* ]]; then
  grep -q '^dtparam=uart0=on' "$CONFIG" || echo 'dtparam=uart0=on' | sudo tee -a "$CONFIG" >/dev/null
  warn "Pi 5: GPIO14/15 UART is /dev/ttyAMA0 — set serial.device: /dev/ttyAMA0 in config/system.yaml"
fi
AFTER="$(cat "$CONFIG" "$CMDLINE" | md5sum)"
rm -f /tmp/rov_reboot_required
if [[ "$BEFORE" != "$AFTER" ]]; then
  touch /tmp/rov_reboot_required
  warn "UART boot config changed — a REBOOT is required"
fi

# ---------- 3. groups, ST-Link udev, sudoers for camera/bridge restart ----------
sudo usermod -aG dialout,plugdev,video "$ME"
if ! ls /lib/udev/rules.d/*stlink* /etc/udev/rules.d/*stlink* >/dev/null 2>&1; then
  say "adding ST-Link udev rule"
  sudo tee /etc/udev/rules.d/49-stlink.rules >/dev/null <<'EOF'
SUBSYSTEMS=="usb", ATTRS{idVendor}=="0483", ATTRS{idProduct}=="3748", MODE="0666", GROUP="plugdev"
SUBSYSTEMS=="usb", ATTRS{idVendor}=="0483", ATTRS{idProduct}=="374b", MODE="0666", GROUP="plugdev"
SUBSYSTEMS=="usb", ATTRS{idVendor}=="0483", ATTRS{idProduct}=="374e", MODE="0666", GROUP="plugdev"
SUBSYSTEMS=="usb", ATTRS{idVendor}=="0483", ATTRS{idProduct}=="374f", MODE="0666", GROUP="plugdev"
SUBSYSTEMS=="usb", ATTRS{idVendor}=="0483", ATTRS{idProduct}=="3752", MODE="0666", GROUP="plugdev"
SUBSYSTEMS=="usb", ATTRS{idVendor}=="0483", ATTRS{idProduct}=="3753", MODE="0666", GROUP="plugdev"
EOF
  sudo udevadm control --reload-rules && sudo udevadm trigger
fi
SYSTEMCTL="$(command -v systemctl)"
TMP="$(mktemp)"
echo "$ME ALL=(root) NOPASSWD: $SYSTEMCTL restart rov-bridge" >"$TMP"
if sudo visudo -cf "$TMP" >/dev/null; then
  sudo install -m 0440 "$TMP" /etc/sudoers.d/rov
fi
rm -f "$TMP"

# ---------- 4. tether address ----------
if [[ $DO_ETH == 1 ]]; then bash "$ROVDIR/pi/setup_eth.sh"; else say "leaving eth0 alone (--no-eth)"; fi

# ---------- 5. service (the bridge also runs the camera, aimed at whichever laptop is talking to it) ----------
if systemctl list-unit-files rov-camera.service >/dev/null 2>&1; then
  sudo systemctl disable --now rov-camera 2>/dev/null || true
  sudo rm -f /etc/systemd/system/rov-camera.service
fi
sed -e "s|@USER@|$ME|g" -e "s|@ROVDIR@|$ROVDIR|g" "$ROVDIR/pi/rov-bridge.service" | sudo tee /etc/systemd/system/rov-bridge.service >/dev/null
chmod +x "$ROVDIR/pi/"*.sh "$ROVDIR/pi/"*.py
sudo systemctl daemon-reload
sudo systemctl enable rov-bridge
sudo systemctl restart rov-bridge
say "rov-bridge installed and restarted"
systemctl --no-pager --lines=0 status rov-bridge || true
[[ -f /tmp/rov_reboot_required ]] && warn "REBOOT the Pi now (UART config changed): sudo reboot"
