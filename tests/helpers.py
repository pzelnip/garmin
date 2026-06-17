"""Shared test data-seeding helpers.

Kept separate from conftest.py so all test modules can
`from helpers import ...` without depending on pytest's conftest import
machinery.

- `make_day` builds (but does not save) a DayStats instance. It's the
  primitive used by the property/unit tests that need an unsaved row, and by
  the seeders below.
- `add_day` is `session.add(make_day(...))` for tests that already hold a
  Session.
- `seed` opens a Session against db.ENGINE and commits rows, for tests that
  drive the app through the Flask client.
"""

from datetime import date

import db
from db import DayStats, Source


def make_day(
    day=date(2026, 1, 1),
    step_count=10_000,
    daily_step_goal=10_000,
    notes="",
    source=Source.garmin,
    **fields,
):
    """Build an unsaved DayStats instance. Required model fields default to a
    goal-met day; any other column (water, sleep, mood, floors, weight, ...)
    can be passed through **fields."""
    return DayStats(
        day=day,
        step_count=step_count,
        daily_step_goal=daily_step_goal,
        notes=notes,
        source=source,
        **fields,
    )


def add_day(session, day=date(2026, 1, 1), **fields):
    """Add one DayStats row to `session` (see make_day for the kwargs)."""
    session.add(make_day(day=day, **fields))


def seed(notes_by_day):
    """Seed a {date: notes} mapping into db.ENGINE (default goal-met steps)."""
    with db.Session(db.ENGINE) as session:
        for day, notes in notes_by_day.items():
            add_day(session, day, notes=notes)
        session.commit()
