#!/usr/bin/env bash
# Passwordless SSH to the Pi + "rovpi" alias. Asks for the Pi password once.
set -euo pipefail
source "$(dirname "$0")/common.sh"
PIIP="$(cfg network.pi_ip)"; PIUSER="$(cfg pi.user)"
mkdir -p ~/.ssh && chmod 700 ~/.ssh
[[ -f ~/.ssh/id_ed25519 ]] || ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519
touch ~/.ssh/config && chmod 600 ~/.ssh/config
if ! grep -q "^Host $PI\$" ~/.ssh/config; then
  cat >>~/.ssh/config <<EOF

Host $PI
    HostName $PIIP
    User $PIUSER
    ConnectTimeout 5
    ServerAliveInterval 5
    StrictHostKeyChecking accept-new
EOF
  say "added '$PI' to ~/.ssh/config"
fi
ssh-copy-id -i ~/.ssh/id_ed25519.pub "$PI"
ssh -o BatchMode=yes "$PI" true && say "ssh $PI works without a password"
