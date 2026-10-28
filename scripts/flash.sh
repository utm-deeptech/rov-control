#!/usr/bin/env bash
# Flash a .bin to the Blackpill through the ST-Link on the Pi.
#   scripts/flash.sh firmware.bin
# Make sure the ROV is DISARMED (the app refuses to flash while armed).
set -uo pipefail
source "$(dirname "$0")/common.sh"
BIN="${1:-}"; [[ -f "$BIN" ]] || die "usage: $0 firmware.bin"
ADDR="$(cfg flash.address)"; TMP="$(cfg flash.remote_tmp)"
say "copying $BIN -> $PI:$TMP"
scp -q "$BIN" "$PI:$TMP" || die "scp failed"
say "st-flash --reset write $TMP $ADDR"
ssh "$PI" "st-flash --reset write $TMP $ADDR" && exit 0
say "retrying with --connect-under-reset"
ssh "$PI" "st-flash --connect-under-reset --reset write $TMP $ADDR"
