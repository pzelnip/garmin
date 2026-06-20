#!/usr/bin/env bash
# Triggered by the dashboard's debug-panel "Force update" button. Does a
# fresh git pull and restarts the service — but does NOT run the metrics
# job or ping healthchecks. For that, use run-garmin.sh.
set -euo pipefail

cd /home/pi/temp/sandbox/garmin
git pull origin main
# Clear python bytecode files to ensure any code changes are picked up.
find . -type d -name __pycache__ -exec rm -rf {} + || true
sudo systemctl restart --no-block garmin.service
