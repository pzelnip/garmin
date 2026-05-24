#!/usr/bin/env python3

import contextlib
import logging
import os
import sys
from datetime import date, datetime, timedelta
from random import random
from time import sleep

from garminconnect import Garmin

from db import DayStats, db_session, get_steps_per_day_from_db, Source
from inputs import date_picker, number_picker, yes_no

logging.basicConfig(stream=sys.stdout, level=logging.INFO)

API = None


def number_of_days_picker():
    return number_picker("How many days in period?", 7)


def get_from_garmin(day: date, session) -> DayStats:
    orig_day = day
    day = day.isoformat()
    logging.info(f"Requesting steps for {day}")

    # throttle a little bit
    sleep(random())

    entry = API.get_stats_and_body(day)
    daystats = DayStats(
        day=orig_day,
        step_count=entry["totalSteps"],
        bmi=entry["bmi"],
        body_fat=entry["bodyFat"],
        body_water=entry["bodyWater"],
        bone_mass=entry["boneMass"],
        muscle_mass=entry["muscleMass"],
        weight_grams=entry["weight"],
        daily_step_goal=entry["dailyStepGoal"],
        distance_traveled_metres=entry["totalDistanceMeters"],
        floors_climbed_goal=entry["userFloorsAscendedGoal"],
        floors_climbed=entry["floorsAscended"],
        floors_descended=entry["floorsDescended"],
        max_heart_rate=entry["maxHeartRate"],
        min_heart_rate=entry["minHeartRate"],
        max_stress=entry["maxStressLevel"],
        resting_heart_rate=entry["restingHeartRate"],
        stress=entry["averageStressLevel"],
        source=Source.garmin,
    )

    session.add(daystats)
    session.commit()
    logging.info(f"Got {daystats.step_count} steps for {day}")
    return daystats


def initialize_api():
    global API  # pylint: disable=global-statement

    logging.info("Logging in with garmin...")
    API = Garmin(os.getenv("GARMIN_EMAIL"), os.getenv("GARMIN_PASSWORD"))
    if not API.login():
        logging.exception("failed to log in, aborting")
        exit(1)
    logging.info("Logged in")


@contextlib.contextmanager
def garmin_api():
    initialize_api()
    yield API


def process_range(start_date: date, days: int):
    dates = {start_date + timedelta(days=i): None for i in range(days)}

    # grab any existing values from the DB
    with db_session() as session:
        for day in dates:
            dates[day] = get_steps_per_day_from_db(day, session)

        # any unknown values read from Garmin
        if remaining_days := [k for k, count in dates.items() if not count]:
            with garmin_api():
                for day in remaining_days:
                    dates[day] = get_from_garmin(day, session)

    return dates.values()


def get_end_date():
    today = datetime.now().date()
    if yes_no(f"Use today ({today}) as end date? ", default=1):
        return today
    return date_picker()


def get_range():
    # end date is yesterday
    if len(sys.argv) > 1 and sys.argv[1] == "--auto":
        today = datetime.now().date()
        end_date = today - timedelta(days=1)
        days = 7
    elif len(sys.argv) > 1 and sys.argv[1] == "--backfill":
        start_date = date.fromisoformat(sys.argv[2])
        end_date = date.fromisoformat(sys.argv[3])
        days = (end_date - start_date).days + 1
        return start_date, days
    else:
        end_date = get_end_date() - timedelta(days=1)
        days = number_of_days_picker()
    start_date = end_date - timedelta(days=days - 1)
    return start_date, days


def main():
    start_date, days = get_range()
    process_range(start_date, days)


if __name__ == "__main__":
    exit(main())
