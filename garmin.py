#!/usr/bin/env python3

import contextlib
import json
import logging
import os
import sys
from datetime import date, timedelta
from random import random
from time import sleep
from typing import Optional

import requests
from bullet import Bullet
from garminconnect import Garmin
from sqlmodel import Field, Session, SQLModel, create_engine, select

logging.basicConfig(stream=sys.stdout, level=logging.INFO)

DAYS_IN_PERIOD = 7
TARGET_STEP_GOAL = 11_000
API = None


class StepEntry(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    day: date = Field(sa_column_kwargs={"unique": True})
    step_count: int
    goal_met: bool


def day_count(day):
    engine = init_db()
    with Session(engine, expire_on_commit=False) as session:
        stmt = select(StepEntry).where(StepEntry.day == day)

        if entry := session.exec(stmt).first():
            logging.info(f"Entry for {day} in DB, returning cached value")
            return entry

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


def summarize(start_date, end_date, data):
    total_steps = sum(x.step_count for x in data)
    goal_days = sum(x.goal_met for x in data)
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
    if not ask_to_post():
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


def ask_to_post():
    return (
        Bullet(
            prompt="\nPost to Step Challenge channel? ",
            choices=["No", "Yes"],
            align=5,
            margin=2,
            bullet="",
            pad_right=5,
        ).launch()
        == "Yes"
    )


def main():
    # end date is yesterday
    end_date = date.today() - timedelta(days=1)
    start_date = end_date - timedelta(days=DAYS_IN_PERIOD - 1)

    with garmin_api():
        # calculate steps between the two dates
        result = [
            day_count(start_date + timedelta(days=i)) for i in range(DAYS_IN_PERIOD)
        ]

    data = summarize(start_date, end_date, result)
    logging.info(f"Data: {data}")
    post_to_zap(data)


if __name__ == "__main__":
    exit(main())
