#!/usr/bin/env bash
# Copy pi/, protocol/, config/ to the Pi, run install.sh there, restart services.
#   scripts/deploy_pi.sh            full install (Pi needs internet for apt)
#   scripts/deploy_pi.sh --no-apt   skip apt (normal case once packages are in)
set -euo pipefail
source "$(dirname "$0")/common.sh"

ssh -o ConnectTimeout=5 "$PI" true || die "cannot ssh to '$PI' — run scripts/setup_ssh.sh first"
say "copying files to $PI:~/$PI_DIR"
ssh "$PI" "mkdir -p ~/$PI_DIR"
if command -v rsync >/dev/null && ssh "$PI" command -v rsync >/dev/null; then
  rsync -az --delete --exclude '__pycache__' --exclude 'test_*.py' --exclude logs \
    "$ROOT/pi" "$ROOT/protocol" "$ROOT/config" "$ROOT/sim" "$PI:$PI_DIR/"
else
  tar -C "$ROOT" --exclude '__pycache__' --exclude logs -czf - pi protocol config sim | ssh "$PI" "tar -C ~/$PI_DIR -xzf -"
fi

say "running install.sh on the Pi (may ask for the Pi's sudo password)"
ssh -t "$PI" "bash ~/$PI_DIR/pi/install.sh $*"

if ssh "$PI" test -f /tmp/rov_reboot_required; then
  echo
  say "The Pi's UART boot config changed and it must REBOOT for the serial port to work."
  read -r -p "Reboot the Pi now? [y/N] " ans
  if [[ "${ans,,}" == y* ]]; then
    ssh "$PI" "sudo reboot" || true
    say "rebooting; wait ~40 s, then: ssh $PI systemctl is-active rov-bridge rov-camera"
  else
    say "OK — reboot it yourself later: ssh $PI sudo reboot"
  fi
else
  sleep 1
  ssh "$PI" "systemctl is-active rov-bridge rov-camera" && say "both services active"
fi
