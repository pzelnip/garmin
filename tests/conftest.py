import pytest
from sqlmodel import Session, SQLModel, create_engine

import db

TEST_PASSWORD = "test-password"


@pytest.fixture(autouse=True)
def force_sqlite(monkeypatch):
    """Guard against tests ever touching a real database.

    `db._init_db()` builds its engine from CONN_STR. We blank it out and point
    db.ENGINE at a throwaway in-memory SQLite engine so that even code paths
    that call db_session()/_init_db() can never reach a production Postgres.
    """
    monkeypatch.setenv("CONN_STR", "sqlite://")
    monkeypatch.setattr(db, "ENGINE", None)
    monkeypatch.setenv("DASHBOARD_PASSWORD", TEST_PASSWORD)


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as sess:
        yield sess
    engine.dispose()  # close pooled sqlite connections (avoids ResourceWarning)


@pytest.fixture
def client(monkeypatch):
    """Flask test client backed by a fresh in-memory SQLite engine.

    Points db.ENGINE at the throwaway engine so route code that opens a
    db_session() reads/writes this DB instead of production Postgres. Use the
    seed helpers below to populate it before issuing requests. Starts logged
    in; tests of the login flow itself should log out first.
    """
    from app import app

    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(db, "ENGINE", engine)

    monkeypatch.setattr(app, "secret_key", "test-secret")

    with app.test_client() as test_client:
        with test_client.session_transaction() as sess:
            sess["_user_id"] = "owner"
        yield test_client

    engine.dispose()
