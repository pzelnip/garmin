from datetime import date, timedelta

from helpers import add_day
from sqlmodel import select

import db
from db import StepTarget

# The `client` fixture (in conftest.py) points db.ENGINE at a fresh in-memory
# SQLite DB, so these exercise the real /api/step-plan routes end to end.


def _seed_target(day, target):
    with db.Session(db.ENGINE) as session:
        session.add(StepTarget(day=day, target=target))
        session.commit()


def test_step_plan_returns_targets_and_actual_steps(client):
    today = date.today()
    yesterday = today - timedelta(days=1)
    with db.Session(db.ENGINE) as session:
        add_day(session, yesterday, step_count=8_000)
        session.commit()
    _seed_target(today, 9_000)

    body = client.get(
        f"/api/step-plan?start={yesterday.isoformat()}&end={today.isoformat()}"
    ).get_json()

    by_day = {d["day"]: d for d in body["days"]}
    assert by_day[yesterday.isoformat()]["steps"] == 8_000
    assert by_day[yesterday.isoformat()]["target"] is None
    assert by_day[today.isoformat()]["target"] == 9_000
    assert by_day[today.isoformat()]["steps"] is None


def test_step_plan_bad_range_params_are_rejected(client):
    assert client.get("/api/step-plan").status_code == 400
    assert client.get("/api/step-plan?start=nope&end=2026-01-01").status_code == 400
    assert (
        client.get("/api/step-plan?start=2026-01-05&end=2026-01-01").status_code == 400
    )


def test_put_sets_target_for_today(client):
    today = date.today()

    res = client.put(f"/api/step-plan/{today.isoformat()}", json={"target": 12_000})

    assert res.status_code == 200
    assert res.get_json()["saved"] is True
    with db.Session(db.ENGINE) as session:
        row = session.exec(select(StepTarget).where(StepTarget.day == today)).first()
        assert row.target == 12_000


def test_put_upserts_existing_target(client):
    future = date.today() + timedelta(days=3)
    _seed_target(future, 5_000)

    client.put(f"/api/step-plan/{future.isoformat()}", json={"target": 6_500})

    with db.Session(db.ENGINE) as session:
        rows = session.exec(select(StepTarget).where(StepTarget.day == future)).all()
    assert len(rows) == 1
    assert rows[0].target == 6_500


def test_put_allows_past_day_for_backfill(client):
    past = date.today() - timedelta(days=5)

    res = client.put(f"/api/step-plan/{past.isoformat()}", json={"target": 10_000})

    assert res.status_code == 200
    with db.Session(db.ENGINE) as session:
        row = session.exec(select(StepTarget).where(StepTarget.day == past)).first()
        assert row.target == 10_000


def test_put_rejects_non_positive_target(client):
    today = date.today().isoformat()
    assert client.put(f"/api/step-plan/{today}", json={"target": 0}).status_code == 400
    assert client.put(f"/api/step-plan/{today}", json={"target": -5}).status_code == 400
    assert (
        client.put(f"/api/step-plan/{today}", json={"target": "x"}).status_code == 400
    )
    assert (
        client.put(f"/api/step-plan/{today}", json={"target": True}).status_code == 400
    )


def _targets_for(days):
    with db.Session(db.ENGINE) as session:
        rows = session.exec(select(StepTarget).where(StepTarget.day.in_(days))).all()
    return {row.day: row.target for row in rows}


def test_bulk_put_sets_target_on_arbitrary_days(client):
    today = date.today()
    days = [today - timedelta(days=4), today, today + timedelta(days=9)]
    _seed_target(days[1], 3_000)

    res = client.put(
        "/api/step-plan",
        json={"days": [d.isoformat() for d in days], "target": 11_000},
    )

    assert res.status_code == 200
    assert res.get_json()["saved"] is True
    assert _targets_for(days) == dict.fromkeys(days, 11_000)


def test_bulk_put_dedupes_repeated_days(client):
    day = date.today() + timedelta(days=1)

    client.put(
        "/api/step-plan",
        json={"days": [day.isoformat(), day.isoformat()], "target": 8_000},
    )

    with db.Session(db.ENGINE) as session:
        rows = session.exec(select(StepTarget).where(StepTarget.day == day)).all()
    assert len(rows) == 1


def test_bulk_put_rejects_bad_payloads(client):
    today = date.today().isoformat()
    assert client.put("/api/step-plan", json={"target": 5_000}).status_code == 400
    assert (
        client.put("/api/step-plan", json={"days": [], "target": 5_000}).status_code
        == 400
    )
    assert (
        client.put(
            "/api/step-plan", json={"days": ["nope"], "target": 5_000}
        ).status_code
        == 400
    )
    assert (
        client.put("/api/step-plan", json={"days": [today], "target": 0}).status_code
        == 400
    )
    assert client.put("/api/step-plan", json={"days": [today]}).status_code == 400


def test_bulk_delete_clears_selected_days_only(client):
    today = date.today()
    cleared = [today - timedelta(days=3), today + timedelta(days=2)]
    kept = today + timedelta(days=5)
    for day in [*cleared, kept]:
        _seed_target(day, 9_000)

    res = client.delete(
        "/api/step-plan", json={"days": [d.isoformat() for d in cleared]}
    )

    assert res.status_code == 200
    assert _targets_for([*cleared, kept]) == {kept: 9_000}


def test_bulk_delete_rejects_bad_payloads(client):
    assert client.delete("/api/step-plan", json={}).status_code == 400
    assert client.delete("/api/step-plan", json={"days": ["x"]}).status_code == 400


def test_delete_clears_future_target(client):
    future = date.today() + timedelta(days=2)
    _seed_target(future, 7_000)

    res = client.delete(f"/api/step-plan/{future.isoformat()}")

    assert res.status_code == 200
    with db.Session(db.ENGINE) as session:
        assert (
            session.exec(select(StepTarget).where(StepTarget.day == future)).first()
            is None
        )


def test_delete_clears_past_day(client):
    past = date.today() - timedelta(days=5)
    _seed_target(past, 7_000)

    res = client.delete(f"/api/step-plan/{past.isoformat()}")

    assert res.status_code == 200
    with db.Session(db.ENGINE) as session:
        assert (
            session.exec(select(StepTarget).where(StepTarget.day == past)).first()
            is None
        )
