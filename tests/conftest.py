import pytest
from sqlmodel import Session, SQLModel, create_engine

import db


@pytest.fixture(autouse=True)
def force_sqlite(monkeypatch):
    """Guard against tests ever touching a real database.

    `db._init_db()` builds its engine from CONN_STR. We blank it out and point
    db.ENGINE at a throwaway in-memory SQLite engine so that even code paths
    that call db_session()/_init_db() can never reach a production Postgres.
    """
    monkeypatch.setenv("CONN_STR", "sqlite://")
    monkeypatch.setattr(db, "ENGINE", None)


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as sess:
        yield sess
    engine.dispose()  # close pooled sqlite connections (avoids ResourceWarning)
