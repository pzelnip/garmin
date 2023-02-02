#!/usr/bin/env python3

import contextlib
import json
import logging
import os
import sys
from datetime import date, datetime, timedelta
from random import random
from time import sleep

import requests
from garminconnect import Garmin
from analytics import find_current_streak

from db import DayStats, StepEntry, db_session, get_steps_per_day_from_db, Source
from inputs import date_picker, number_picker, yes_no

logging.basicConfig(stream=sys.stdout, level=logging.INFO)

TARGET_STEP_GOAL = 11_000
API = None


def number_of_days_picker():
    return number_picker("How many days in period?", 7)


def get_from_garmin(day: date, session) -> DayStats:
    orig_day = day
    day = day.isoformat()
    logging.info(f"Requesting steps for {day}")

    # Use get_daily_steps, sample response:
    #
    # [
    #     {
    #         "calendarDate": "2023-01-29",
    #         "stepGoal": 11000,
    #         "totalDistance": 14170,
    #         "totalSteps": 16643,
    #     },
    #     {
    #         "calendarDate": "2023-01-30",
    #         "stepGoal": 11000,
    #         "totalDistance": 8160,
    #         "totalSteps": 9755,
    #     },
    # ]
    #

    # We just request one day, so assume one entry in the list
    entry = API.get_daily_steps(day, day)[0]

    steps = entry["totalSteps"]
    target_step_goal = entry["stepGoal"]

    # throttle a little bit
    sleep(random())

    entry = StepEntry(
        day=orig_day,
        step_count=steps,
        goal_met=steps > target_step_goal,
    )
    session.add(entry)
    to_return = entry

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
    to_return = daystats

    return to_return


def summarize(start_date, end_date, data, days, current_streak):
    total_steps = sum(x.step_count for x in data)
    goal_days = sum(x.step_goal_met for x in data)
    streak_data = {}
    if current_streak:
        streak_data = {
            "days": current_streak.days,
            "start": current_streak.start.strftime("%-d-%b-%Y"),
            "end": current_streak.end.strftime("%-d-%b-%Y"),
        }
    return {
        "start_date": f"{start_date:%-d-%b-%Y}",
        "end_date": f"{end_date:%-d-%b-%Y}",
        "step_total": f"{total_steps:,}",
        "step_average": f"{total_steps // days:,}",
        "num_days_goal_met": goal_days,
        "days_in_period": days,
        "percent_goal_met": f"{(goal_days/days* 100):.1f}%",
        "username": "aparkin",
        "streak": streak_data,
    }


def initialize_api():
    global API

    logging.info("Logging in with garmin...")
    API = Garmin(os.getenv("GARMIN_EMAIL"), os.getenv("GARMIN_PASSWORD"))
    if not API.login():
        logging.exception("failed to log in, aborting")
        exit(1)
    logging.info("Logged in")


@contextlib.contextmanager
def garmin_api():
    global API
    initialize_api()
    yield API


def post_to_zap(data, url):
    logging.info(f"Data: {data}")
    if not yes_no("Post to Step Challenge channel?", 0):
        return

    headers = {"Content-type": "application/json"}
    data = json.dumps(data)
    logging.info(f"Posting to zap -- {url} - data {json.dumps(data)}")
    result = requests.post(url, data=data, headers=headers)
    logging.info(f"Response: {result.status_code} - {result.json()}")


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


def main():
    # end date is yesterday
    end_date = get_end_date() - timedelta(days=1)
    days = number_of_days_picker()
    start_date = end_date - timedelta(days=days - 1)

    result = process_range(start_date, days)
    current_streak = find_current_streak()

    data = summarize(start_date, end_date, result, days, current_streak)

    post_to_zap(data, os.getenv("ZAPIER_WEEKLY_ZAP_HOOK_URL", None))


if __name__ == "__main__":
    exit(main())
