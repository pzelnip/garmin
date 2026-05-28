#!/usr/bin/env bash
set -euo pipefail

cd /home/pi/temp/sandbox/garmin
set -a
source ./.envrc
set +a

exec ./.venv/bin/python src/app.py
