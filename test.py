#!/usr/bin/env python3

from datetime import date, timedelta
import contextlib
import json
import os

from garminconnect import Garmin


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
    print(
        f"For the period from {start_date} to {end_date}, averaged {avg_daily:,} "
        f"steps per day, for a total of {total_steps:,} steps.  Step goal met on "
        f"{goal_days}/{DAYS_IN_PERIOD} ({(goal_days/DAYS_IN_PERIOD* 100):.1f}%) of days."
    )


def initialize_api():
    global API
    restored_session = None
    with contextlib.suppress(Exception):
        with open("session_data.json", "r") as fobj:
            restored_session = json.loads(fobj.readlines()[0])
    API = Garmin(
        os.getenv("GARMIN_EMAIL"),
        os.getenv("GARMIN_PASSWORD"),
        session_data=restored_session,
    )
    API.login()


def shutdown():
    # rewrite the session data
    with open("session_data.json", "w") as fobj:
        fobj.write(json.dumps(API.session_data))


@contextlib.contextmanager
def garmin_api():
    initialize_api()
    try:
        yield
    finally:
        shutdown()


def main():
    with garmin_api():
        end_date = date.today()
        start_date = end_date - timedelta(days=DAYS_IN_PERIOD)
        result = [
            day_count(start_date + timedelta(days=i)) for i in range(DAYS_IN_PERIOD)
        ]

        # For testing:
        # result = [
        #     {"day": "2022-10-10", "steps": 14935, "goal_met": True},
        #     {"day": "2022-10-11", "steps": 12244, "goal_met": True},
        #     {"day": "2022-10-12", "steps": 7226, "goal_met": False},
        #     {"day": "2022-10-13", "steps": 14679, "goal_met": True},
        #     {"day": "2022-10-14", "steps": 17332, "goal_met": True},
        #     {"day": "2022-10-15", "steps": 13393, "goal_met": True},
        #     {"day": "2022-10-16", "steps": 14130, "goal_met": True},
        # ]

        summarize(start_date, end_date, result)


if __name__ == "__main__":
    exit(main())
