#!/usr/bin/env python3

import contextlib
import json
import logging
import os
import sys
from calendar import monthrange
from datetime import date, datetime, timedelta
from enum import Enum, auto
from random import random
from time import sleep
from typing import Optional

import requests
from bullet import Bullet, Numbers, ScrollBar, YesNo, colors
from garminconnect import Garmin
from sqlmodel import Field, Session, SQLModel, create_engine, select

logging.basicConfig(stream=sys.stdout, level=logging.INFO)

TARGET_STEP_GOAL = 11_000
API = None


def date_picker(number_of_years=15):
    class State(Enum):
        PICK_YEAR = auto()
        PICK_MONTH = auto()
        PICK_DAY = auto()
        DONE = auto()

    BACK_STRING = "... previous"

    def scrollbar(prompt, options, return_index=False, allow_previous=True):
        print("\n")

        result = ScrollBar(
            f"{prompt}: ",
            options + [BACK_STRING] if allow_previous else options,
            height=5,
            align=5,
            margin=0,
            pointer="👉 ",
            background_on_switch=colors.background["default"],
            word_on_switch=colors.foreground["default"],
            return_index=True,
        ).launch()

        # if picked BACK_STRING return None
        if result[1] == len(options):
            return None

        # Otherwise int the result
        return int(result[1]) if return_index else int(result[0])

    def pick_year():
        current_year = datetime.now().year

        years = [
            str(i) for i in range(current_year, current_year - number_of_years, -1)
        ]
        return scrollbar("Year", years, allow_previous=False)

    def pick_month():
        months = "January February March April May June July August September October November December".split()
        result = scrollbar("Month", months, return_index=True)
        return result if result is None else result + 1

    def pick_day(year, month):
        max_days = monthrange(year, month)[1]
        days = [str(i) for i in range(1, max_days + 1)]
        return scrollbar("Day", days)

    state = State.PICK_YEAR
    while state != State.DONE:
        if state == State.PICK_YEAR:
            year = pick_year()
            state = State.PICK_MONTH
        elif state == State.PICK_MONTH:
            month = pick_month()
            state = State.PICK_DAY if month else State.PICK_YEAR
        elif state == State.PICK_DAY:
            day = pick_day(year, month)
            state = State.DONE if day else State.PICK_MONTH

    return date(year, month, day)


class StepEntry(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    day: date = Field(sa_column_kwargs={"unique": True})
    step_count: int
    goal_met: bool


def get_from_db(day: date) -> StepEntry:
    engine = init_db()
    with Session(engine, expire_on_commit=False) as session:
        stmt = select(StepEntry).where(StepEntry.day == day)

        if entry := session.exec(stmt).first():
            logging.info(f"Entry for {day} in DB, returning cached value")
            return entry

    return None


def get_from_garmin(day: date) -> StepEntry:
    engine = init_db()
    with Session(engine, expire_on_commit=False) as session:
        orig_day = day
        day = day.isoformat()
        logging.info(f"Requesting steps for {day}")
        steps = API.get_steps_data(day)
        # throttle a little bit
        sleep(random())

        total_steps_for_day = sum(x["steps"] for x in steps)
        entry = StepEntry(
            day=orig_day,
            step_count=total_steps_for_day,
            goal_met=total_steps_for_day > TARGET_STEP_GOAL,
        )

        session.add(entry)
        session.commit()

    return entry


def summarize(start_date, end_date, data, days):
    total_steps = sum(x.step_count for x in data)
    goal_days = sum(x.goal_met for x in data)
    return {
        "start_date": f"{start_date:%-d-%b-%Y}",
        "end_date": f"{end_date:%-d-%b-%Y}",
        "step_total": f"{total_steps:,}",
        "step_average": f"{total_steps // days:,}",
        "num_days_goal_met": goal_days,
        "days_in_period": days,
        "percent_goal_met": f"{(goal_days/days* 100):.1f}%",
        "username": "aparkin",
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
    initialize_api()
    yield


def post_to_zap(data):
    if not yes_no("Post to Step Challenge channel?", 0):
        return

    # Step challenge channel url
    url = os.getenv("ZAPIER_WEBHOOK_URL", None)

    headers = {"Content-type": "application/json"}
    data = json.dumps(data)
    logging.info(f"Posting to zap -- {url} - data {json.dumps(data)}")
    result = requests.post(url, data=data, headers=headers)
    logging.info(f"Response: {result.status_code} - {result.json()}")


def init_db():
    engine = create_engine("sqlite:///database.db")
    SQLModel.metadata.create_all(engine)
    return engine


def number_of_days_picker():
    return Numbers("How many days in period? ", type=int).launch(default=7)


def process_range(start_date: date, days: int):
    dates = {start_date + timedelta(days=i): None for i in range(days)}

    # grab any existing values from the DB
    for day in dates:
        dates[day] = get_from_db(day)

    # any unknown values read from Garmin
    if remaining_days := [k for k, count in dates.items() if not count]:
        with garmin_api():
            for day in remaining_days:
                dates[day] = get_from_garmin(day)

    return dates.values()


def yes_no(prompt, default):
    return (
        Bullet(
            prompt=f"\n{prompt} ",
            choices=["No", "Yes"],
            align=5,
            margin=2,
            bullet="",
            pad_right=5,
        ).launch(default=default)
        == "Yes"
    )


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

    data = summarize(start_date, end_date, result, days)
    logging.info(f"Data: {data}")
    post_to_zap(data)


if __name__ == "__main__":
    exit(main())
