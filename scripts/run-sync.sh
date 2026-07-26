#!/usr/bin/env bash
# Triggered by the dashboard's debug-panel "Sync now" button. Runs the
# metrics job (garmin.py --auto) immediately without a git pull, service
# restart, or healthcheck ping — handy just after midnight to pull the
# previous day's data without waiting for the 5 AM cron. Idempotent: the
# job upserts by day and skips days that already have Garmin data.
set -euo pipefail

cd /home/pi/temp/sandbox/garmin

set -a
source ./.envrc
set +a

./.venv/bin/python src/garmin.py --auto
