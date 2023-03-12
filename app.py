import atexit
import os
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from flask import Flask
from jinja2 import Environment, FileSystemLoader, select_autoescape

from sqlmodel import select

from db import StepsToday, _init_db, db_session
from so_far_today import retrieve_steps_at_hour

DEBUG = True

app = Flask(__name__)


# Start and end hour for the graph
START_HOUR = 9
END_HOUR = 22


# Make sure scheduler is only started once
# See: https://stackoverflow.com/a/25519547/808804
if not DEBUG or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
    scheduler = BackgroundScheduler()
    scheduler.add_job(retrieve_steps_at_hour, CronTrigger.from_crontab("0 9-22 * * *"))
    scheduler.start()
    atexit.register(lambda: scheduler.shutdown())


def none_to_null(iterable):
    """Convert None to 'null' in a list of values."""
    return str(iterable).replace("None", "null")


def get_hourly_steps():
    today = datetime.now().date()
    with db_session() as session:
        stmt = (
            select(StepsToday)
            .where(StepsToday.day == today)
            .where(StepsToday.hour >= START_HOUR)
            .where(StepsToday.hour <= END_HOUR)
            .order_by(StepsToday.hour)
        )

        # Pre-allocate array to allow missing entries
        steps = [None] * (END_HOUR - START_HOUR + 1)
        for entry in session.exec(stmt):
            steps[entry.hour - START_HOUR] = entry.step_count

    return steps


@app.route("/")
def step_progress():
    env = Environment(
        loader=FileSystemLoader("."),
        autoescape=select_autoescape(["html", "xml"]),
    )
    env.filters["none_to_null"] = none_to_null
    return env.get_template("graph.jinja2").render(
        hourly_step_data=get_hourly_steps(),
        start_hour=START_HOUR,
        end_hour=END_HOUR,
    )


if __name__ == "__main__":
    _init_db()
    app.run(debug=DEBUG, port=9329)
