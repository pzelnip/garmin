#!/usr/bin/env python3

import contextlib
import logging
import os
import sys
from datetime import date, datetime, timedelta
from random import random
from time import sleep

from garminconnect import Garmin

from db import DayStats, Source, db_session, get_steps_per_day_from_db
from inputs import date_picker, number_picker, yes_no

logging.basicConfig(stream=sys.stdout, level=logging.INFO)

API = None


def number_of_days_picker():
    return number_picker("How many days in period?", 7)


def _round_or_none(v):
    return round(v) if v is not None else None


def get_from_garmin(day: date, session) -> DayStats:
    orig_day = day
    day = day.isoformat()
    logging.info(f"Requesting steps for {day}")

    # throttle a little bit
    sleep(random())

    entry = API.get_stats_and_body(day)

    try:
        hydration = API.get_hydration_data(day) or {}
    except Exception as ex:  # pylint: disable=broad-except
        logging.warning(f"Could not fetch hydration for {day}: {ex}")
        hydration = {}

    try:
        sleep_data = API.get_sleep_data(day) or {}
    except Exception as ex:  # pylint: disable=broad-except
        logging.warning(f"Could not fetch sleep for {day}: {ex}")
        sleep_data = {}

    sleep_dto = sleep_data.get("dailySleepDTO") or {}
    sleep_score = ((sleep_dto.get("sleepScores") or {}).get("overall") or {}).get(
        "value"
    )

    # UPSERT: may already exist as a manual-entry stub created from the
    # dashboard's notes/mood panel for today, before the sync ran. In that
    # case we update the Garmin-sourced fields onto it and preserve notes
    # and mood_score. Otherwise insert a fresh row.
    daystats = get_steps_per_day_from_db(orig_day, session)
    if daystats is None:
        daystats = DayStats(
            day=orig_day, step_count=0, daily_step_goal=0, source=Source.garmin
        )
        session.add(daystats)

    daystats.step_count = entry["totalSteps"]
    daystats.bmi = entry["bmi"]
    daystats.body_fat = entry["bodyFat"]
    daystats.body_water = entry["bodyWater"]
    daystats.bone_mass = entry["boneMass"]
    daystats.muscle_mass = entry["muscleMass"]
    daystats.weight_grams = entry["weight"]
    daystats.daily_step_goal = entry["dailyStepGoal"]
    daystats.distance_traveled_metres = entry["totalDistanceMeters"]
    daystats.floors_climbed_goal = entry["userFloorsAscendedGoal"]
    daystats.floors_climbed = entry["floorsAscended"]
    daystats.floors_descended = entry["floorsDescended"]
    daystats.max_heart_rate = entry["maxHeartRate"]
    daystats.min_heart_rate = entry["minHeartRate"]
    daystats.max_stress = entry["maxStressLevel"]
    daystats.resting_heart_rate = entry["restingHeartRate"]
    daystats.stress = entry["averageStressLevel"]
    daystats.water_consumed_ml = _round_or_none(hydration.get("valueInML"))
    daystats.water_goal_ml = _round_or_none(hydration.get("goalInML"))
    daystats.sleep_total_seconds = sleep_dto.get("sleepTimeSeconds")
    daystats.sleep_deep_seconds = sleep_dto.get("deepSleepSeconds")
    daystats.sleep_light_seconds = sleep_dto.get("lightSleepSeconds")
    daystats.sleep_rem_seconds = sleep_dto.get("remSleepSeconds")
    daystats.sleep_awake_seconds = sleep_dto.get("awakeSleepSeconds")
    daystats.sleep_score = sleep_score
    daystats.source = Source.garmin  # flips from manual_entry on upsert

    session.commit()
    logging.info(f"Got {daystats.step_count} steps for {day}")
    return daystats


def initialize_api():
    global API  # pylint: disable=global-statement

    logging.info("Logging in with garmin...")
    API = Garmin(os.getenv("GARMIN_EMAIL"), os.getenv("GARMIN_PASSWORD"))
    if not API.login():
        logging.exception("failed to log in, aborting")
        sys.exit(1)
    logging.info("Logged in")


@contextlib.contextmanager
def garmin_api():
    initialize_api()
    yield API


def process_range(start_date: date, days: int):
    dates = {start_date + timedelta(days=i): None for i in range(days)}

    # grab any existing values from the DB. Manual-entry stubs (created by
    # the dashboard's notes/mood panel before the Garmin sync ran) don't
    # count as "already have Garmin data" — leave them for the fetch loop
    # below so the upsert lands the real values.
    with db_session() as session:
        for day in dates:
            existing = get_steps_per_day_from_db(day, session)
            if existing is not None and existing.source != Source.manual_entry:
                dates[day] = existing

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
    sys.exit(main())
