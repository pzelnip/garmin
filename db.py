import contextlib
import enum
import logging
import os
import sys
from datetime import date
from typing import List, Optional

from sqlmodel import Column, Enum, Field, Session, SQLModel, create_engine, select

logging.basicConfig(stream=sys.stdout, level=logging.INFO)


class StepEntry(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    day: date = Field(sa_column_kwargs={"unique": True})
    step_count: int
    goal_met: bool


class Source(enum.Enum):
    manual_entry = 0
    garmin = 1
    fitbit = 2


class DayStats(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    day: date = Field(sa_column_kwargs={"unique": True})

    # from get_stats_and_body()
    step_count: int  # totalSteps
    daily_step_goal: int  # dailyStepGoal

    bmi: Optional[float]  # bmi
    body_fat: Optional[float]  # bodyFat
    body_water: Optional[float]  # bodyWater
    bone_mass: Optional[int]  # boneMass
    muscle_mass: Optional[int]  # muscleMass
    weight_grams: Optional[float]  # weight
    distance_traveled_metres: Optional[int]  # totalDistanceMeters
    floors_climbed_goal: Optional[int]  # userFloorsAscendedGoal
    floors_climbed: Optional[float]  # floorsAscended
    floors_descended: Optional[float]  # floorsDescended
    max_heart_rate: Optional[int]  # maxHeartRate
    min_heart_rate: Optional[int]  # minHeartRate
    max_stress: Optional[int]  #  maxStressLevel
    resting_heart_rate: Optional[int]  # restingHeartRate
    stress: Optional[int]  # averageStressLevel

    source: Source = Field(sa_column=Column(Enum(Source)))  # where the data came from

    @property
    def step_goal_met(self):
        return self.step_count >= self.daily_step_goal

    @property
    def floors_climbed_goal_met(self):
        return self.floors_climbed >= self.floors_climbed_goal

    @property
    def weight_pounds(self):
        return self.weight_grams * 0.00220462


def _init_db():
    engine = create_engine(os.getenv("CONN_STR", ""))
    SQLModel.metadata.create_all(engine)
    return engine


@contextlib.contextmanager
def db_session():
    logging.info("Starting DB Session")
    engine = _init_db()
    with Session(engine, expire_on_commit=False) as session:
        yield session
    logging.info("Closing DB Session")


def get_steps_per_day_from_db(day: date, session) -> DayStats:
    stmt = select(DayStats).where(DayStats.day == day)

    if entry := session.exec(stmt).first():
        logging.info(f"Entry for {day} in DB, returning cached value")
        return entry

    return None


def get_all_entries() -> List[StepEntry]:
    with db_session() as session:
        stmt = select(StepEntry).order_by(StepEntry.day)
        return list(session.exec(stmt))
