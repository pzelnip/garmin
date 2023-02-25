#!/usr/bin/env python3

import sys
from datetime import datetime, timedelta
from last_day_for_weekly_post import last_day
from db import db_session, get_day_stats_for_date_range

GOALS = list(range(100_000, 130_000, 5_000))


def from_goal(so_far, target, days_left):
    remaining = target - so_far
    per_day = remaining / days_left
    print(f"From {so_far:,} steps to {target:,} steps in {days_left} days:")
    print(
        f"{remaining:,} steps left to reach {target:,} steps in {days_left} days {round(per_day):,} steps per day"
    )
    print("-" * 80)


def main():
    steps_today = int(sys.argv[1])
    start = last_day().date()
    today = datetime.now().date()
    next_sunday = today + timedelta((6 - today.weekday()) % 7)
    with db_session() as session:
        days = get_day_stats_for_date_range(session, start + timedelta(days=1), today)

    steps_so_far = sum(day.step_count for day in days)
    for goal in GOALS:
        from_goal(steps_so_far + steps_today, goal, (next_sunday - today).days)


if __name__ == "__main__":
    exit(main())
