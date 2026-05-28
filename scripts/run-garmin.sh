#!/usr/bin/env bash
set -euo pipefail

cd /home/pi/temp/sandbox/garmin
git pull origin main

set -a
source ./.envrc
set +a

./.venv/bin/python src/garmin.py --auto

curl -fsS -m 10 --retry 3 "$HEALTHCHECKS_URL"
