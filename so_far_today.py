import os
from datetime import datetime

from garminconnect import Garmin

from db import StepsToday, db_session


def main():
    API = Garmin(os.getenv("GARMIN_EMAIL"), os.getenv("GARMIN_PASSWORD"))
    if not API.login():
        raise ValueError("failed to login")

    now = datetime.now()
    today = now.date()
    hour = now.hour
    steps = API.get_daily_steps(today, today)[0]["totalSteps"]
    with db_session() as session:
        entry = StepsToday(day=today, step_count=steps, hour=hour, retrieved_at=now)
        session.add(entry)
        session.commit()


if __name__ == "__main__":
    exit(main())
