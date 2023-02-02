#!/usr/bin/env python3

import logging
import os
import sys
from datetime import datetime, timedelta

from db import db_session, get_day_stats_for_date_range
from garmin import post_to_zap

logging.basicConfig(stream=sys.stdout, level=logging.INFO)


def get_last_month_day_entries():
    last_day_of_last_month = datetime.now().replace(day=1).date() - timedelta(days=1)
    first_day_of_last_month = last_day_of_last_month.replace(day=1)

    with db_session() as session:
        entries_for_month = get_day_stats_for_date_range(
            session, first_day_of_last_month, last_day_of_last_month
        )

    total_steps = sum(e.step_count for e in entries_for_month)
    avg_per_day = total_steps / last_day_of_last_month.day
    week1_steps, week2_steps, week3_steps, week4_steps, week5_steps = 0, 0, 0, 0, 0
    month = first_day_of_last_month.month

    week1_steps = sum(e.step_count for e in entries_for_month if e.day.day <= 7)
    week2_steps = sum(e.step_count for e in entries_for_month if 7 < e.day.day <= 14)
    week3_steps = sum(e.step_count for e in entries_for_month if 14 < e.day.day <= 21)
    week4_steps = sum(e.step_count for e in entries_for_month if 21 < e.day.day <= 28)
    week5_steps = sum(e.step_count for e in entries_for_month if e.day.day > 28)

    post_to_zap(
        {
            "month_str": first_day_of_last_month.strftime("%B"),
            "month_num": month,
            "username": "aparkin",
            "total_steps": f"{total_steps:,}",
            "average_per_day": f"{avg_per_day:,.2f}",
            "week1_steps": f"{week1_steps:,}",
            "week2_steps": f"{week2_steps:,}",
            "week3_steps": f"{week3_steps:,}",
            "week4_steps": f"{week4_steps:,}",
            "week5_steps": f"{week5_steps:,}",
        },
        os.getenv("ZAPIER_MONTHLY_ZAP_HOOK_URL", None),
    )


def main():
    get_last_month_day_entries()


if __name__ == "__main__":
    exit(main())
