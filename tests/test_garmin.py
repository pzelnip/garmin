"""Tests for the Garmin ingest/upsert path.

Regression coverage for the manual-entry stub interaction: a notes/mood row
created from the dashboard before the morning sync (source=manual_entry,
step_count=0) must NOT block the Garmin fetch, and when the fetch lands it
must UPSERT onto that row — preserving notes/mood while flipping source to
garmin. This broke once when the guard was reverted (a sync saw the 0-step
stub, treated it as cached, and skipped Garmin).
"""

from datetime import date

import pytest
from sqlmodel import Session, SQLModel, create_engine

import db
import garmin
from db import DayStats, Source, get_steps_per_day_from_db
from helpers import make_day
from sqlmodel import select

# Minimal stats payload covering every key get_from_garmin reads.
GARMIN_STATS = {
    "totalSteps": 12154,
    "bmi": 23.0,
    "bodyFat": 18.0,
    "bodyWater": 55.0,
    "boneMass": 3000,
    "muscleMass": 30000,
    "weight": 80000,
    "dailyStepGoal": 10000,
    "totalDistanceMeters": 10074,
    "userFloorsAscendedGoal": 10,
    "floorsAscended": 12,
    "floorsDescended": 11,
    "maxHeartRate": 150,
    "minHeartRate": 45,
    "maxStressLevel": 80,
    "restingHeartRate": 53,
    "averageStressLevel": 30,
}


class FakeGarminAPI:
    def get_stats_and_body(self, day):
        return GARMIN_STATS

    def get_hydration_data(self, day):
        return {"valueInML": 1500, "goalInML": 2000}

    def get_sleep_data(self, day):
        return {"dailySleepDTO": {"sleepTimeSeconds": 28560}}


@pytest.fixture(autouse=True)
def no_throttle(monkeypatch):
    # get_from_garmin sleeps random() seconds to throttle; skip it in tests.
    monkeypatch.setattr(garmin, "sleep", lambda *_: None)


@pytest.fixture
def fake_api(monkeypatch):
    monkeypatch.setattr(garmin, "API", FakeGarminAPI())


def test_get_from_garmin_upserts_onto_manual_entry_stub(session, fake_api):
    stub = make_day(
        day=date(2026, 6, 21),
        step_count=0,
        daily_step_goal=0,
        notes="Father's day brunch",
        mood_score=7,
        source=Source.manual_entry,
    )
    session.add(stub)
    session.commit()

    result = garmin.get_from_garmin(date(2026, 6, 21), session)

    assert result.step_count == 12154
    assert result.source == Source.garmin
    assert result.notes == "Father's day brunch"
    assert result.mood_score == 7
    assert len(session.exec(select(DayStats)).all()) == 1


def test_get_from_garmin_inserts_when_no_existing_row(session, fake_api):
    result = garmin.get_from_garmin(date(2026, 6, 21), session)

    assert result.step_count == 12154
    assert result.source == Source.garmin


def test_process_range_refetches_manual_entry_stub(monkeypatch):
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(db, "ENGINE", engine)
    with Session(engine) as seed_session:
        seed_session.add(
            make_day(
                day=date(2026, 6, 21),
                step_count=0,
                daily_step_goal=0,
                notes="Father's day brunch",
                mood_score=7,
                source=Source.manual_entry,
            )
        )
        seed_session.commit()

    import contextlib

    @contextlib.contextmanager
    def fake_garmin_api():
        yield None

    monkeypatch.setattr(garmin, "garmin_api", fake_garmin_api)
    monkeypatch.setattr(garmin, "API", FakeGarminAPI())

    garmin.process_range(date(2026, 6, 21), 1)

    with Session(engine) as check:
        row = get_steps_per_day_from_db(date(2026, 6, 21), check)
        assert row.step_count == 12154
        assert row.source == Source.garmin
        assert row.notes == "Father's day brunch"
        assert row.mood_score == 7
    engine.dispose()


def test_process_range_skips_garmin_for_already_synced_day(monkeypatch):
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(db, "ENGINE", engine)
    with Session(engine) as seed_session:
        seed_session.add(
            make_day(day=date(2026, 6, 21), step_count=9999, source=Source.garmin)
        )
        seed_session.commit()

    import contextlib

    @contextlib.contextmanager
    def boom():
        raise AssertionError("garmin_api() should not be called for a synced day")
        yield

    monkeypatch.setattr(garmin, "garmin_api", boom)

    garmin.process_range(date(2026, 6, 21), 1)

    with Session(engine) as check:
        row = get_steps_per_day_from_db(date(2026, 6, 21), check)
        assert row.step_count == 9999
    engine.dispose()
