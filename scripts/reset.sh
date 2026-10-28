#!/usr/bin/env bash
# Reset the Blackpill through the ST-Link plugged into the Pi.
set -uo pipefail
source "$(dirname "$0")/common.sh"
say "st-flash reset on $PI"
ssh "$PI" "st-flash reset" && exit 0
say "retrying with --connect-under-reset"
ssh "$PI" "st-flash --connect-under-reset reset"
