from datetime import date

from helpers import add_day, seed
from sqlmodel import select

import db
from db import DayStats

# The `client` fixture lives in tests/conftest.py; the seed helpers live in
# tests/helpers.py so the render tests can share them.


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


# ------------- Tests for day detail (GET /api/day/<iso>) ------------


def test_day_detail_returns_found_false_for_unseeded_day(client):
    response = client.get("/api/day/2026-01-01")

    body = response.get_json()
    assert response.status_code == 200
    assert body["found"] is False
    assert body["day"] == "2026-01-01"


def test_day_detail_returns_full_fields_for_seeded_day(client):
    with db.Session(db.ENGINE) as session:
        add_day(
            session,
            date(2026, 1, 1),
            step_count=12_345,
            water_consumed_ml=2000,
            water_goal_ml=2500,
            sleep_total_seconds=27_000,
            sleep_score=84,
            mood_score=7,
            notes="felt good",
        )
        session.commit()

    response = client.get("/api/day/2026-01-01")

    body = response.get_json()
    assert response.status_code == 200
    assert body["found"] is True
    assert body["steps"] == 12_345
    assert body["water_consumed_ml"] == 2000
    assert body["water_goal_met"] is False
    assert body["sleep_total_h"] == 7.5
    assert body["sleep_score"] == 84
    assert body["mood_score"] == 7
    assert body["notes"] == "felt good"


def test_day_detail_rejects_malformed_date(client):
    response = client.get("/api/day/not-a-date")

    assert response.status_code == 400
    assert "error" in response.get_json()


# ------------- Tests for day update (PUT /api/day/<iso>) ------------


def test_update_day_persists_notes_for_existing_day(client):
    with db.Session(db.ENGINE) as session:
        add_day(session, date(2026, 1, 1), notes="old")
        session.commit()

    response = client.put("/api/day/2026-01-01", json={"notes": "new text"})

    body = response.get_json()
    assert response.status_code == 200
    assert body["saved"] is True
    assert body["notes"] == "new text"
    with db.Session(db.ENGINE) as session:
        row = session.exec(
            select(DayStats).where(DayStats.day == date(2026, 1, 1))
        ).first()
        assert row.notes == "new text"


def test_update_day_sets_mood_score(client):
    with db.Session(db.ENGINE) as session:
        add_day(session, date(2026, 1, 1))
        session.commit()

    response = client.put("/api/day/2026-01-01", json={"mood_score": 8})

    body = response.get_json()
    assert response.status_code == 200
    assert body["mood_score"] == 8
    with db.Session(db.ENGINE) as session:
        row = session.exec(
            select(DayStats).where(DayStats.day == date(2026, 1, 1))
        ).first()
        assert row.mood_score == 8


def test_update_day_writes_notes_and_mood_together(client):
    with db.Session(db.ENGINE) as session:
        add_day(session, date(2026, 1, 1))
        session.commit()

    response = client.put(
        "/api/day/2026-01-01", json={"notes": "ran 5k", "mood_score": 9}
    )

    body = response.get_json()
    assert response.status_code == 200
    assert body["notes"] == "ran 5k"
    assert body["mood_score"] == 9
    with db.Session(db.ENGINE) as session:
        row = session.exec(
            select(DayStats).where(DayStats.day == date(2026, 1, 1))
        ).first()
        assert row.notes == "ran 5k"
        assert row.mood_score == 9


def test_update_day_leaves_omitted_fields_untouched(client):
    with db.Session(db.ENGINE) as session:
        add_day(session, date(2026, 1, 1), notes="keep me", mood_score=5)
        session.commit()

    # Only mood supplied — notes must be preserved.
    response = client.put("/api/day/2026-01-01", json={"mood_score": 7})

    assert response.status_code == 200
    with db.Session(db.ENGINE) as session:
        row = session.exec(
            select(DayStats).where(DayStats.day == date(2026, 1, 1))
        ).first()
        assert row.notes == "keep me"
        assert row.mood_score == 7


def test_update_day_clears_mood_with_null(client):
    with db.Session(db.ENGINE) as session:
        add_day(session, date(2026, 1, 1), mood_score=5)
        session.commit()

    response = client.put("/api/day/2026-01-01", json={"mood_score": None})

    assert response.status_code == 200
    with db.Session(db.ENGINE) as session:
        row = session.exec(
            select(DayStats).where(DayStats.day == date(2026, 1, 1))
        ).first()
        assert row.mood_score is None


def test_update_day_creates_stub_row_for_today(client):
    today = date.today()

    response = client.put(
        f"/api/day/{today.isoformat()}", json={"notes": "hi", "mood_score": 6}
    )

    assert response.status_code == 200
    with db.Session(db.ENGINE) as session:
        row = session.exec(select(DayStats).where(DayStats.day == today)).first()
        assert row is not None
        assert row.notes == "hi"
        assert row.mood_score == 6


def test_update_day_404_for_missing_past_day(client):
    response = client.put("/api/day/2020-01-01", json={"notes": "hi"})

    assert response.status_code == 404


def test_update_day_rejects_empty_payload(client):
    with db.Session(db.ENGINE) as session:
        add_day(session, date(2026, 1, 1))
        session.commit()

    response = client.put("/api/day/2026-01-01", json={})

    assert response.status_code == 400


def test_update_day_rejects_non_string_notes(client):
    with db.Session(db.ENGINE) as session:
        add_day(session, date(2026, 1, 1))
        session.commit()

    response = client.put("/api/day/2026-01-01", json={"notes": 123})

    assert response.status_code == 400


def test_update_day_rejects_out_of_range_mood(client):
    with db.Session(db.ENGINE) as session:
        add_day(session, date(2026, 1, 1))
        session.commit()

    response = client.put("/api/day/2026-01-01", json={"mood_score": 11})

    assert response.status_code == 400


def test_update_day_rejects_boolean_mood(client):
    with db.Session(db.ENGINE) as session:
        add_day(session, date(2026, 1, 1))
        session.commit()

    response = client.put("/api/day/2026-01-01", json={"mood_score": True})

    assert response.status_code == 400


def test_update_day_invalidates_dashboard_cache(client, monkeypatch):
    import app as app_module

    calls = []
    monkeypatch.setattr(
        app_module, "invalidate_dashboard_cache", lambda: calls.append(True)
    )
    with db.Session(db.ENGINE) as session:
        add_day(session, date(2026, 1, 1))
        session.commit()

    client.put("/api/day/2026-01-01", json={"notes": "x"})

    assert calls == [True]


# ------------- Tests for force-update (POST /api/force-update) ------------


def test_force_update_spawns_script_without_running_it(client, monkeypatch):
    import app as app_module

    spawned = []
    monkeypatch.setattr(app_module.os.path, "isfile", lambda path: True)
    monkeypatch.setattr(
        app_module.subprocess, "Popen", lambda *args, **kwargs: spawned.append(args)
    )

    response = client.post("/api/force-update")

    assert response.status_code == 200
    assert response.get_json() == {"started": True}
    assert len(spawned) == 1


def test_force_update_500_when_script_missing(client, monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module.os.path, "isfile", lambda path: False)

    response = client.post("/api/force-update")

    assert response.status_code == 500
    assert "error" in response.get_json()
