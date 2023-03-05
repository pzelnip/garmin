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


# Make sure scheduler is only started once
# See: https://stackoverflow.com/a/25519547/808804
if not DEBUG or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
    scheduler = BackgroundScheduler()
    scheduler.add_job(retrieve_steps_at_hour, CronTrigger.from_crontab("0 9-22 * * *"))
    scheduler.start()
    atexit.register(lambda: scheduler.shutdown())


def get_hourly_steps():
    today = datetime.now().date()
    with db_session() as session:
        stmt = (
            select(StepsToday).where(StepsToday.day == today).order_by(StepsToday.hour)
        )

        steps = [r.step_count for r in session.exec(stmt)]
    return steps


@app.route("/")
def step_progress():
    env = Environment(
        loader=FileSystemLoader("."),
        autoescape=select_autoescape(["html", "xml"]),
    )
    return env.get_template("graph.jinja2").render(hourly_step_data=get_hourly_steps())


if __name__ == "__main__":
    _init_db()
    app.run(debug=DEBUG, port=9329)
