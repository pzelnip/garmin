#!/usr/bin/env python3
"""One-off discovery probe: capture sample responses from Garmin's
lifestyle-logging endpoint so we can decide how to model the data.

Hits:
  - API.get_lifestyle_logging_data(day) for a handful of dates
  - A couple speculative sibling URLs via API.connectapi(...) to see if
    we can list configured behaviours / fetch a date range

No DB writes; output goes to stdout. Run via:
    source ./.envrc
    cd src && PYTHONPATH=. ../.venv/bin/python ../misc_scripts/probe_lifestyle.py
"""

import json
import logging
import sys
from datetime import datetime, timedelta
from random import random
from time import sleep

from garmin import API, garmin_api  # noqa: F401 — API is the global

logging.basicConfig(stream=sys.stdout, level=logging.INFO)


def _dump(label, value):
    print(f"\n===== {label} =====")
    try:
        print(json.dumps(value, indent=2, default=str))
    except (TypeError, ValueError):
        print(repr(value))


def main():
    today = datetime.now().date()
    probe_dates = [
        today - timedelta(days=1),   # yesterday
        today - timedelta(days=3),   # mid-week
        today - timedelta(days=7),   # 7 days ago
        today - timedelta(days=30),  # ~1 month ago
    ]

    with garmin_api() as api:
        for day in probe_dates:
            day_str = day.isoformat()
            sleep(random())  # throttle, same as get_from_garmin
            try:
                data = api.get_lifestyle_logging_data(day_str)
                _dump(f"get_lifestyle_logging_data({day_str})", data)
            except Exception as ex:
                logging.warning(f"{day_str}: get_lifestyle_logging_data failed: {ex}")

        # Speculative siblings — these may not exist; 404s expected, just want to see.
        speculative_urls = [
            "/lifestylelogging-service/behaviors",
            f"/lifestylelogging-service/dailyLog/range/{(today - timedelta(days=7)).isoformat()}/{today.isoformat()}",
            "/lifestylelogging-service/dailyLog",
            "/lifestylelogging-service/dailySummary",
        ]
        for url in speculative_urls:
            sleep(random())
            try:
                data = api.connectapi(url)
                _dump(f"connectapi({url})", data)
            except Exception as ex:
                logging.warning(f"connectapi({url}) failed: {ex}")


if __name__ == "__main__":
    main()
