#!/usr/bin/env bash
# One-command install for Ubuntu 22.04+:  bash install.sh
# apt packages (asks for sudo) + .venv + menu launcher + runs the tests.
set -euo pipefail
cd "$(dirname "$0")"
bash scripts/setup_laptop.sh
make desktop
make test
echo
echo "Done. Open 'ROV Control' from the applications menu, or run:  make run-sim   (practice, no ROV)   |   make run"
