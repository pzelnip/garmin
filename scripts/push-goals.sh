#!/usr/bin/env bash
# Publish the local goals.json into the Neon `goals` table that backs the
# dashboard's Goals tab. Edit goals.json, run this — no deploy / commit needed.
# Writes to production Neon by design; see misc_scripts/push_goals.py.
#
# Run this locally (from the Mac dev checkout), not on the Pi.
set -euo pipefail

cd "$(dirname "$0")/.."
set -a
source ./.envrc
set +a

cd src && PYTHONPATH=. ../.venv/bin/python ../misc_scripts/push_goals.py
