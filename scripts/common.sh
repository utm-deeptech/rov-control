# shellcheck shell=bash
# Sourced by the other scripts. Reads config/system.yaml.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/.venv/bin/python"; [[ -x "$PY" ]] || PY=python3
cfg() { "$PY" "$ROOT/config/cfg.py" "$1"; }
PI="$(cfg pi.ssh_alias)"
PI_DIR="$(cfg pi.install_dir)"
say() { echo -e "\e[1;36m[rov]\e[0m $*"; }
die() { echo -e "\e[1;31m[rov] $*\e[0m" >&2; exit 1; }
