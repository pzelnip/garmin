from datetime import date

import pytest
from sqlmodel import SQLModel, create_engine, select

import db
from app import app
from db import DayStats, Source


@pytest.fixture
def client(monkeypatch):
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(db, "ENGINE", engine)

    with app.test_client() as test_client:
        yield test_client

    engine.dispose()


def add_day(session, day, notes="", step_count=10_000):
    session.add(
        DayStats(
            day=day,
            step_count=step_count,
            daily_step_goal=10_000,
            notes=notes,
            source=Source.garmin,
        )
    )


def seed(notes_by_day):
    with db.Session(db.ENGINE) as session:
        for day, notes in notes_by_day.items():
            add_day(session, day, notes=notes)
        session.commit()


# ------------- Tests for search ------------


def test_search_returns_empty_results_for_empty_query(client):
    response = client.get("/api/notes/search?q=")

    body = response.get_json()
    assert response.status_code == 200
    assert body["results"] == []
    assert body["too_short"] is True


def test_search_returns_empty_results_for_whitespace_query(client):
    response = client.get("/api/notes/search?q=%20%20")

    body = response.get_json()
    assert response.status_code == 200
    assert body["results"] == []
    assert body["too_short"] is True


def test_search_returns_too_short_for_one_or_two_character_query(client):
    seed({date(2026, 1, 1): "abc"})

    response = client.get("/api/notes/search?q=ab")

    body = response.get_json()
    assert response.status_code == 200
    assert body["too_short"] is True
    assert body["results"] == []


def test_search_matches_substring_case_insensitively(client):
    seed(
        {
            date(2026, 1, 1): "Went for a Run in the park",
            date(2026, 1, 2): "ran errands all day",
            date(2026, 1, 3): "no exercise today",
        }
    )

    response = client.get("/api/notes/search?q=RUN")

    body = response.get_json()
    assert response.status_code == 200
    assert [r["day"] for r in body["results"]] == ["2026-01-01"]


def test_search_excludes_days_with_empty_or_non_matching_notes(client):
    seed(
        {
            date(2026, 1, 1): "",
            date(2026, 1, 2): "totally unrelated text",
            date(2026, 1, 3): "found the keyword here",
        }
    )

    response = client.get("/api/notes/search?q=keyword")

    body = response.get_json()
    assert [r["day"] for r in body["results"]] == ["2026-01-03"]


def test_search_orders_results_newest_first(client):
    seed(
        {
            date(2026, 1, 1): "yoga session",
            date(2026, 3, 1): "yoga session",
            date(2026, 2, 1): "yoga session",
        }
    )

    response = client.get("/api/notes/search?q=yoga")

    body = response.get_json()
    assert [r["day"] for r in body["results"]] == [
        "2026-03-01",
        "2026-02-01",
        "2026-01-01",
    ]


def test_search_match_offsets_point_at_query_within_snippet(client):
    seed({date(2026, 1, 1): "before keyword after"})

    response = client.get("/api/notes/search?q=keyword")

    body = response.get_json()
    result = body["results"][0]
    snippet = result["snippet"]
    start = result["match_start"]
    end = start + body["match_len"]
    assert snippet[start:end] == "keyword"


def test_search_returns_match_len_at_response_root_not_per_result(client):
    seed(
        {
            date(2026, 1, 1): "yoga session",
            date(2026, 1, 2): "more yoga today",
        }
    )

    response = client.get("/api/notes/search?q=yoga")

    body = response.get_json()
    assert body["match_len"] == len("yoga")
    for result in body["results"]:
        assert "match_len" not in result


def test_search_truncates_long_notes_with_ellipsis(client):
    notes = ("filler text " * 50) + "needle" + (" filler text" * 50)
    seed({date(2026, 1, 1): notes})

    response = client.get("/api/notes/search?q=needle")

    body = response.get_json()
    result = body["results"][0]
    assert result["snippet"].startswith("…")
    assert result["snippet"].endswith("…")
    assert "needle" in result["snippet"]
    assert len(result["snippet"]) < len(notes)


def test_search_treats_sql_wildcard_percent_literally(client):
    seed(
        {
            date(2026, 1, 1): "shoot for 100% goal completion",
            date(2026, 1, 2): "no percent sign here at all",
        }
    )

    response = client.get("/api/notes/search?q=100%25")

    body = response.get_json()
    assert [r["day"] for r in body["results"]] == ["2026-01-01"]


def test_search_treats_sql_wildcard_underscore_literally(client):
    seed(
        {
            date(2026, 1, 1): "tag_name was logged",
            date(2026, 1, 2): "tagXname was something else",
        }
    )

    response = client.get("/api/notes/search?q=tag_name")

    body = response.get_json()
    assert [r["day"] for r in body["results"]] == ["2026-01-01"]


def test_search_is_safe_from_sql_injection(client):
    seed(
        {
            date(2026, 1, 1): "innocent note",
            date(2026, 1, 2): "another innocent note",
        }
    )

    response = client.get("/api/notes/search?q='; DROP TABLE daystats; --")

    body = response.get_json()
    assert response.status_code == 200
    assert body["results"] == []
    with db.Session(db.ENGINE) as session:
        assert len(list(session.exec(select(DayStats)))) == 2
