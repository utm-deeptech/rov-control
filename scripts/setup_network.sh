#!/usr/bin/env bash
# Give the FXTI's USB-Ethernet interface the static laptop address (no gateway,
# so your Wi-Fi internet keeps working). Run after plugging in the FXTI.
#   scripts/setup_network.sh            -> lists interfaces and asks
#   scripts/setup_network.sh enx00e04c  -> uses that interface
set -euo pipefail
source "$(dirname "$0")/common.sh"
CIDR="$(cfg network.laptop_cidr)"; PIIP="$(cfg network.pi_ip)"
IFACE="${1:-}"
if [[ -z "$IFACE" ]]; then
  echo "Ethernet interfaces (the FXTI usually shows up as enx...):"
  nmcli -t -f DEVICE,TYPE,STATE device | awk -F: '$2=="ethernet"{print "  "$1"  ("$3")"}'
  read -r -p "Interface for the tether: " IFACE
fi
[[ -n "$IFACE" ]] || die "no interface given"
nmcli connection delete rov-tether >/dev/null 2>&1 || true
nmcli connection add type ethernet ifname "$IFACE" con-name rov-tether \
  ipv4.method manual ipv4.addresses "$CIDR" ipv4.never-default yes ipv6.method disabled autoconnect yes
nmcli connection up rov-tether
say "$IFACE = $CIDR. Pinging the Pi at $PIIP..."
ping -c 3 -W 1 "$PIIP" || echo "No reply yet: is the Pi powered and set to $PIIP? (see README)"
