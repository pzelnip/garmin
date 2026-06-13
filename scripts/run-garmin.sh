#!/usr/bin/env bash
set -euo pipefail

cd /home/pi/temp/sandbox/garmin
git pull origin main

# Clear python bytecode files to ensure any code changes are picked up.
find . -type d -name __pycache__ -exec rm -rf {} +

set -a
source ./.envrc
set +a

./.venv/bin/python src/garmin.py --auto

curl -fsS -m 10 --retry 3 "$HEALTHCHECKS_URL"
