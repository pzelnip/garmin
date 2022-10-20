#!/usr/bin/env python3

import contextlib
import json
import logging
import os
import sys
from datetime import date, timedelta
from random import random
from time import sleep

import requests
from garminconnect import Garmin


logging.basicConfig(stream=sys.stdout, level=logging.INFO)

DAYS_IN_PERIOD = 7
TARGET_STEP_GOAL = 11_000
API = None


def day_count(day):
    day = day.isoformat()
    logging.info(f"Requesting steps for {day}")
    steps = API.get_steps_data(day)
    # throttle a little bit
    sleep(random())

    total_steps_for_day = sum(x["steps"] for x in steps)
    return {
        "day": day,
        "steps": total_steps_for_day,
        "goal_met": total_steps_for_day > TARGET_STEP_GOAL,
    }


def summarize(start_date, end_date, data):
    total_steps = sum(x["steps"] for x in data)
    goal_days = sum(x["goal_met"] for x in data)
    return {
        "start_date": f"{start_date:%-d-%b-%Y}",
        "end_date": f"{end_date:%-d-%b-%Y}",
        "step_total": f"{total_steps:,}",
        "step_average": f"{total_steps // DAYS_IN_PERIOD:,}",
        "num_days_goal_met": goal_days,
        "days_in_period": DAYS_IN_PERIOD,
        "percent_goal_met": f"{(goal_days/DAYS_IN_PERIOD* 100):.1f}%",
        "username": "aparkin",
    }


def initialize_api():
    global API

    logging.info("Logging in with garmin...")
    API = Garmin(
        os.getenv("GARMIN_EMAIL"),
        os.getenv("GARMIN_PASSWORD"),
        session_data=read_session(),
    )
    if not API.login():
        logging.exception("failed to log in, aborting")
        exit(1)
    logging.info("Logged in")


def read_session():
    restored_session = None
    with contextlib.suppress(Exception):
        with open("session_data.json", "r") as fobj:
            restored_session = json.loads(fobj.readlines()[0])
    return restored_session


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


def post_to_zap(data):
    url = "https://hooks.zapier.com/hooks/catch/12432035/b0lwp7z/"
    headers = {"Content-type": "application/json"}
    logging.info(f"Posting to zap -- {url}")
    result = requests.post(url, data=data, headers=headers)
    logging.info(f"Response: {result.status_code} - {result.json()}")


def main():
    with garmin_api():
        end_date = date.today()
        start_date = end_date - timedelta(days=DAYS_IN_PERIOD)
        result = [
            day_count(start_date + timedelta(days=i)) for i in range(DAYS_IN_PERIOD)
        ]
        data = summarize(start_date, end_date, result)
        logging.info(f"Data: {data}")
        post_to_zap(data)


if __name__ == "__main__":
    exit(main())
