#!/usr/bin/env python3
"""One-off: backfill sleep_* fields on existing DayStats rows.

Iterates every DayStats row where sleep_total_seconds IS NULL (most-recent
first), fetches that day's sleep from Garmin, and UPDATEs the sleep columns.
Rows where Garmin has no sleep data (e.g. device not worn) stay NULL.

Run once after the sleep schema migration is applied. Subsequent --auto /
--backfill runs of garmin.py populate sleep on new rows directly, so this
script shouldn't need to run again.

Usage:
    .venv/bin/python src/backfill_sleep.py [--dry-run]

Pass --dry-run to log what would happen without writing anything to the DB.
"""

import logging
import sys
from random import random
from time import sleep

from sqlmodel import select

from db import DayStats, db_session
from garmin import garmin_api

logging.basicConfig(stream=sys.stdout, level=logging.INFO)


def main():
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        logging.info("DRY RUN — no DB writes will be made")

    with db_session() as session:
        stmt = (
            select(DayStats)
            .where(DayStats.sleep_total_seconds.is_(None))
            .order_by(DayStats.day.desc())
        )
        rows = list(session.exec(stmt))

    logging.info(f"Found {len(rows)} rows needing sleep backfill")
    if not rows:
        return

    updated = 0
    no_data = 0
    failed = 0

    with garmin_api() as api:
        with db_session() as session:
            for row in rows:
                day_iso = row.day.isoformat()
                logging.info(f"Fetching sleep for {day_iso}")
                sleep(random())  # throttle

                try:
                    sleep_data = api.get_sleep_data(day_iso) or {}
                except Exception as ex:
                    logging.warning(f"{day_iso}: fetch failed: {ex}")
                    failed += 1
                    continue

                dto = sleep_data.get("dailySleepDTO") or {}
                total = dto.get("sleepTimeSeconds")

                if total is None:
                    logging.info(f"{day_iso}: no sleep data, leaving NULL")
                    no_data += 1
                    continue

                score = ((dto.get("sleepScores") or {}).get("overall") or {}).get(
                    "value"
                )

                values = {
                    "sleep_total_seconds": total,
                    "sleep_deep_seconds": dto.get("deepSleepSeconds"),
                    "sleep_light_seconds": dto.get("lightSleepSeconds"),
                    "sleep_rem_seconds": dto.get("remSleepSeconds"),
                    "sleep_awake_seconds": dto.get("awakeSleepSeconds"),
                    "sleep_score": score,
                }

                if dry_run:
                    logging.info(f"{day_iso}: would update {values} (dry-run)")
                else:
                    db_row = session.get(DayStats, row.id)
                    for k, v in values.items():
                        setattr(db_row, k, v)
                    session.add(db_row)
                    session.commit()
                    logging.info(f"{day_iso}: updated {values}")
                updated += 1

    logging.info(
        f"Done. updated={updated} no_data={no_data} failed={failed} "
        f"total_scanned={len(rows)}"
    )


if __name__ == "__main__":
    main()
