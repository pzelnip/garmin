#!/usr/bin/env bash
# Interactive Goals-tab editor. Opens goals.json in VS Code; validates + publishes
# to Neon on every save; commits + pushes when you close the tab. See
# misc_scripts/edit_goals.py.
#
# Run this locally (from the Mac dev checkout), not on the Pi.
set -euo pipefail

cd "$(dirname "$0")/.."
set -a
source ./.envrc
set +a

cd src && PYTHONPATH=. ../.venv/bin/python ../misc_scripts/edit_goals.py
