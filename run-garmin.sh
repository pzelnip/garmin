#!/usr/bin/env bash
set -euo pipefail

cd /home/pi/temp/sandbox/garmin
set -a
source ./.envrc
set +a

./.venv/bin/python garmin.py --auto

curl -fsS -m 10 --retry 3 "$HEALTHCHECKS_URL"
