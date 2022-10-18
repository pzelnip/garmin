#!/usr/bin/env python3

import contextlib
import json
import logging
import os
import sys
from datetime import date, timedelta

from garminconnect import Garmin


logging.basicConfig(stream=sys.stdout, level=logging.INFO)

DAYS_IN_PERIOD = 7
TARGET_STEP_GOAL = 11_000
API = None


def day_count(day):
    day = day.isoformat()
    steps = API.get_steps_data(day)
    total_steps_for_day = sum(x["steps"] for x in steps)
    return {
        "day": day,
        "steps": total_steps_for_day,
        "goal_met": total_steps_for_day > TARGET_STEP_GOAL,
    }


def summarize(start_date, end_date, data):
    total_steps = sum(x["steps"] for x in data)
    avg_daily = total_steps // DAYS_IN_PERIOD
    goal_days = sum(x["goal_met"] for x in data)
    logging.info(
        f"For the period from {start_date} to {end_date}, averaged {avg_daily:,} "
        f"steps per day, for a total of {total_steps:,} steps.  Step goal met on "
        f"{goal_days}/{DAYS_IN_PERIOD} ({(goal_days/DAYS_IN_PERIOD* 100):.1f}%) of days."
    )


def initialize_api():
    global API
    logged_in = False

    # Try logging in with session data
    with contextlib.suppress(Exception):
        with open("session_data.json", "r") as fobj:
            restored_session = json.loads(fobj.readlines()[0])
        API = Garmin("", "", session_data=restored_session)
        logged_in = API.login()

    # if failed to log in, fallback to user/pass authentication
    if not logged_in:
        logging.warning("Failed to restore session, re-logging in")
        API = Garmin(os.getenv("GARMIN_EMAIL"), os.getenv("GARMIN_PASSWORD"))
        try:
            if API.login():
                return
            else:
                raise RuntimeError("Failed to log in")
        except Exception as e:
            logging.exception(f"failed to log in ({e}), aborting")
            exit(1)


def write_session():
    # Write out session data for next run
    session_data = json.dumps(API.session_data)
    with open("session_data.json", "w") as fobj:
        fobj.write(session_data)


@contextlib.contextmanager
def garmin_api():
    initialize_api()
    try:
        yield
    finally:
        write_session()


def main():
    with garmin_api():
        end_date = date.today()
        start_date = end_date - timedelta(days=DAYS_IN_PERIOD)
        result = [
            day_count(start_date + timedelta(days=i)) for i in range(DAYS_IN_PERIOD)
        ]
        summarize(start_date, end_date, result)


if __name__ == "__main__":
    exit(main())
