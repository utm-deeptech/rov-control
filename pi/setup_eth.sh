#!/usr/bin/env bash
# ON THE PI: give eth0 (the FXTI tether side) the static ROV address. No gateway, so Wi-Fi keeps the internet.
# Works with NetworkManager (Raspberry Pi OS Bookworm) and netplan (Ubuntu). Safe to re-run.
set -euo pipefail
ROVDIR="$(cd "$(dirname "$0")/.." && pwd)"
cfg() { python3 "$ROVDIR/config/cfg.py" "$1"; }
IP="$(cfg network.pi_ip)"; PREFIX="$(cfg network.laptop_cidr | cut -d/ -f2)"; IF="${1:-eth0}"
if systemctl is-active --quiet NetworkManager && command -v nmcli >/dev/null; then
  sudo nmcli connection delete rov-tether >/dev/null 2>&1 || true
  sudo nmcli connection add type ethernet ifname "$IF" con-name rov-tether ipv4.method manual \
    ipv4.addresses "$IP/$PREFIX" ipv4.never-default yes ipv6.method disabled autoconnect yes >/dev/null
  sudo nmcli connection up rov-tether >/dev/null 2>&1 || echo "(tether not plugged in yet: it will come up when it is)"
elif command -v netplan >/dev/null; then
  sudo tee /etc/netplan/60-rov-tether.yaml >/dev/null <<NP
network:
  version: 2
  ethernets:
    $IF:
      dhcp4: false
      addresses: [$IP/$PREFIX]
      optional: true
NP
  sudo chmod 600 /etc/netplan/60-rov-tether.yaml
  sudo netplan apply
else
  echo "No NetworkManager or netplan found: set $IF to $IP/$PREFIX by hand"; exit 1
fi
echo "$IF = $IP/$PREFIX (tether). Laptop side: make network"
