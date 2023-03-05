import atexit
import os
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from flask import Flask
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, select

from db import StepsToday, _init_db
from so_far_today import retrieve_steps_at_hour

DEBUG = True

app = Flask(__name__)
engine = _init_db()


# Make sure scheduler is only started once
# See: https://stackoverflow.com/a/25519547/808804
if not DEBUG or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
    scheduler = BackgroundScheduler()
    scheduler.add_job(retrieve_steps_at_hour, CronTrigger.from_crontab("0 9-22 * * *"))
    scheduler.start()
    atexit.register(lambda: scheduler.shutdown())


def get_hourly_steps():
    today = datetime.now().date()
    attempts = 1
    # sqlalchmy sucks at handling db connection errors
    while True:
        try:
            with Session(engine, expire_on_commit=False) as session:
                stmt = (
                    select(StepsToday)
                    .where(StepsToday.day == today)
                    .order_by(StepsToday.hour)
                )

                steps = [r.step_count for r in session.exec(stmt)]
            break
        except OperationalError as e:
            print(f"error connecting to db, retrying -- {e}")
            attempts += 1
            if attempts > 3:
                print("failed to connect to db, giving up")
                raise
    return steps


@app.route("/")
def step_progress():
    env = Environment(
        loader=FileSystemLoader("."),
        autoescape=select_autoescape(["html", "xml"]),
    )
    return env.get_template("graph.jinja2").render(hourly_step_data=get_hourly_steps())


if __name__ == "__main__":
    app.run(debug=DEBUG, port=9329)
