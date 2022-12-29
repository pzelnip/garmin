import contextlib
from datetime import date
import logging
import sys
from typing import List, Optional

from sqlmodel import Field, Session, SQLModel, create_engine, select

logging.basicConfig(stream=sys.stdout, level=logging.INFO)


class StepEntry(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    day: date = Field(sa_column_kwargs={"unique": True})
    step_count: int
    goal_met: bool


def _init_db():
    engine = create_engine("sqlite:///database.db")
    SQLModel.metadata.create_all(engine)
    return engine


@contextlib.contextmanager
def db_session():
    engine = _init_db()
    with Session(engine, expire_on_commit=False) as session:
        yield session


def get_steps_per_day_from_db(day: date) -> StepEntry:
    with db_session() as session:
        stmt = select(StepEntry).where(StepEntry.day == day)

        if entry := session.exec(stmt).first():
            logging.info(f"Entry for {day} in DB, returning cached value")
            return entry

    return None


def get_all_entries() -> List[StepEntry]:
    with db_session() as session:
        stmt = select(StepEntry).order_by(StepEntry.day)
        return list(session.exec(stmt))
