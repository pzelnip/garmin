import contextlib
import enum
import logging
import os
import sys
from datetime import date
from typing import List, Optional

from sqlalchemy import JSON, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import defer
from sqlmodel import Column, Enum, Field, Session, SQLModel, create_engine, select

logging.basicConfig(stream=sys.stdout, level=logging.INFO)


ENGINE = None
NUM_RETRIES = 3


class Source(enum.Enum):
    """Origin of a DayStats row.

    Indicates whether the day's data came from the Garmin API, was entered
    by hand, or was imported from Fitbit. Used to distinguish synthesized or
    backfilled rows from authoritative device data.
    """

    manual_entry = 0
    garmin = 1
    fitbit = 2


class DayStats(SQLModel, table=True):
    """Daily summary of activity, body, hydration, sleep, and health metrics for one day.

    One row per calendar day (the `day` column is unique). Populated by
    `garmin.py` from the Garmin Connect `get_stats_and_body`,
    `get_hydration_data`, and `get_sleep_data` endpoints; step_count and
    daily_step_goal are required, all other biometric, hydration, and
    sleep fields are optional since they depend on which features the
    user's device and apps capture (e.g. weight requires a connected
    scale; hydration requires manual intake logging in the Garmin Connect
    app; sleep requires the device to be worn at night).
    """

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

    water_consumed_ml: Optional[int]  # valueInML (rounded)
    water_goal_ml: Optional[int]  # goalInML (rounded)

    sleep_total_seconds: Optional[int]  # dailySleepDTO.sleepTimeSeconds
    sleep_deep_seconds: Optional[int]  # dailySleepDTO.deepSleepSeconds
    sleep_light_seconds: Optional[int]  # dailySleepDTO.lightSleepSeconds
    sleep_rem_seconds: Optional[int]  # dailySleepDTO.remSleepSeconds
    sleep_awake_seconds: Optional[int]  # dailySleepDTO.awakeSleepSeconds
    sleep_score: Optional[int]  # dailySleepDTO.sleepScores.overall.value

    # Freeform per-day notes, edited from the Day tab in the dashboard.
    # NOT NULL with empty-string default — new Garmin ingests omit the kwarg
    # and rely on this default rather than passing it explicitly.
    notes: str = Field(default="", sa_column_kwargs={"server_default": ""})

    # 1-10 self-rated mood score, edited from the Day tab in the dashboard.
    # Nullable since most existing rows won't have one and it's a manual entry.
    mood_score: Optional[int] = None

    source: Source = Field(sa_column=Column(Enum(Source)))  # where the data came from

    @property
    def step_goal_met(self):
        return self.step_count >= self.daily_step_goal

    @property
    def floors_climbed_goal_met(self):
        if self.floors_climbed is None or self.floors_climbed_goal is None:
            return False
        return self.floors_climbed >= self.floors_climbed_goal

    @property
    def weight_pounds(self):
        if self.weight_grams is None:
            return None
        return self.weight_grams * 0.00220462

    @property
    def water_goal_met(self):
        if self.water_consumed_ml is None or self.water_goal_ml is None:
            return False
        return self.water_consumed_ml >= self.water_goal_ml

    def match_snippet(self, query, radius=60):
        """Returns a snippet of the notes around the first match of the query, case-insensitively."""

        idx = self.notes.lower().find(query)
        start = max(0, idx - radius)
        end = min(len(self.notes), idx + len(query) + radius)
        snippet = (
            ("…" if start > 0 else "")
            + self.notes[start:end]
            + ("…" if end < len(self.notes) else "")
        )
        return snippet, idx - start + (1 if start > 0 else 0)


class Goals(SQLModel, table=True):
    """Single-row config table holding the Goals-tab ladder as one JSON blob.

    Deliberately outside the daily-sync `DayStats` data model: the ladder is
    hand-authored content, not device data. It lives in the DB (rather than a
    committed file) so it can be updated without a code deploy or a git-pull
    merge conflict on the Pi — edit the local `goals.json` with `make
    edit-goals`, which validates it and calls `push_goals.push` to upsert the
    single row here. Read fresh on every dashboard render.

    The column is JSONB on Postgres and generic JSON on SQLite (tests), so
    `create_all` works against both.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    data: dict = Field(
        sa_column=Column(JSON().with_variant(JSONB(), "postgresql"), nullable=False)
    )


def get_goals_data() -> dict | None:
    """Return the goals-ladder JSON blob from the single-row `goals` table, or
    None if the table is empty. Orders by id so a stray extra row can't shadow
    the canonical (lowest-id) one."""
    with db_session() as session:
        row = session.exec(select(Goals).order_by(Goals.id)).first()
        return row.data if row else None


def _init_db():
    global ENGINE
    if not ENGINE:
        logging.info("Init DB Engine")
        ENGINE = create_engine(os.getenv("CONN_STR", ""), pool_pre_ping=True)
        SQLModel.metadata.create_all(ENGINE)


def _try_select_one(session):
    try:
        logging.info("Trying select 1")
        # Do a SELECT 1 to make sure the connection is alive because
        # sqlalchemy sucks at connection errors
        session.execute(text("SELECT 1"))
        return True
    except OperationalError as e:
        logging.warning(f"error connecting to db -- {e}")
        return False


@contextlib.contextmanager
def db_session():
    logging.info("Starting DB Session")
    _init_db()
    with Session(ENGINE, expire_on_commit=False) as session:
        # Try NUM_RETRIES times to connect to the DB, if it fails, raise an error
        if not any(_try_select_one(session) for _ in range(NUM_RETRIES)):
            raise RuntimeError("Failed to connect to DB")
        yield session
    logging.info("Closing DB Session")


def get_steps_per_day_from_db(day: date, session) -> DayStats | None:
    stmt = select(DayStats).where(DayStats.day == day)

    if entry := session.exec(stmt).first():
        logging.info(
            f"Entry for {day} in DB, returning cached value ({entry.step_count})"
        )
        return entry

    return None


def get_all_entries(include_notes: bool = True) -> List[DayStats]:
    with db_session() as session:
        stmt = select(DayStats).order_by(DayStats.day)
        if not include_notes:
            stmt = stmt.options(defer(DayStats.notes))
        return list(session.exec(stmt))


def get_day_stats_for_date_range(session, start_date, end_date):
    stmt = (
        select(DayStats)
        .where(DayStats.day >= start_date)
        .where(DayStats.day <= end_date)
        .order_by(DayStats.day)
    )
    return list(session.exec(stmt))
