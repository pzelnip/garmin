from datetime import date

import pytest
from helpers import make_day
from sqlalchemy import inspect

from db import (
    db_session,
    get_all_entries,
    get_day_stats_for_date_range,
    get_steps_per_day_from_db,
)


def test_get_day_stats_for_date_range_is_inclusive_of_both_bounds(session):
    session.add(make_day(day=date(2023, 1, 10)))
    session.add(make_day(day=date(2023, 1, 15)))
    session.add(make_day(day=date(2023, 1, 20)))
    session.commit()

    result = get_day_stats_for_date_range(session, date(2023, 1, 10), date(2023, 1, 20))

    assert [r.day for r in result] == [
        date(2023, 1, 10),
        date(2023, 1, 15),
        date(2023, 1, 20),
    ]


def test_get_day_stats_for_date_range_excludes_days_outside_range(session):
    session.add(make_day(day=date(2023, 1, 9)))
    session.add(make_day(day=date(2023, 1, 12)))
    session.add(make_day(day=date(2023, 1, 21)))
    session.commit()

    result = get_day_stats_for_date_range(session, date(2023, 1, 10), date(2023, 1, 20))

    assert [r.day for r in result] == [date(2023, 1, 12)]


def test_get_day_stats_for_date_range_orders_by_day_ascending(session):
    session.add(make_day(day=date(2023, 1, 18)))
    session.add(make_day(day=date(2023, 1, 11)))
    session.add(make_day(day=date(2023, 1, 14)))
    session.commit()

    result = get_day_stats_for_date_range(session, date(2023, 1, 10), date(2023, 1, 20))

    assert [r.day for r in result] == [
        date(2023, 1, 11),
        date(2023, 1, 14),
        date(2023, 1, 18),
    ]


def test_get_day_stats_for_date_range_empty_when_no_match(session):
    session.add(make_day(day=date(2023, 1, 1)))
    session.commit()

    result = get_day_stats_for_date_range(session, date(2023, 2, 1), date(2023, 2, 28))

    assert not result


# ------------- Tests for DayStats.match_snippet ------------


def test_match_snippet_returns_full_notes_when_shorter_than_radius():
    row = make_day(notes="short note about a run")

    snippet, match_start = row.match_snippet("run")

    assert snippet == "short note about a run"
    assert snippet[match_start : match_start + len("run")] == "run"


def test_match_snippet_lowercases_notes_when_matching():
    row = make_day(notes="Went for a RUN today")

    snippet, match_start = row.match_snippet("run")

    assert snippet[match_start : match_start + len("run")] == "RUN"


def test_match_snippet_returns_leading_ellipsis_when_match_far_from_start():
    notes = ("filler text " * 20) + "needle at the end"
    row = make_day(notes=notes)

    snippet, match_start = row.match_snippet("needle")

    assert snippet.startswith("…")
    assert not snippet.endswith("…")
    assert snippet[match_start : match_start + len("needle")] == "needle"


def test_match_snippet_returns_trailing_ellipsis_when_match_far_from_end():
    notes = "needle at the start" + (" filler text" * 20)
    row = make_day(notes=notes)

    snippet, match_start = row.match_snippet("needle")

    assert not snippet.startswith("…")
    assert snippet.endswith("…")
    assert snippet[match_start : match_start + len("needle")] == "needle"


def test_match_snippet_wraps_match_in_both_ellipses_when_far_from_both_ends():
    notes = ("filler text " * 20) + "needle" + (" filler text" * 20)
    row = make_day(notes=notes)

    snippet, match_start = row.match_snippet("needle")

    assert snippet.startswith("…")
    assert snippet.endswith("…")
    assert snippet[match_start : match_start + len("needle")] == "needle"
    assert len(snippet) < len(notes)


def test_match_snippet_uses_first_match_when_query_appears_multiple_times():
    row = make_day(notes="first run, then another run later")

    snippet, match_start = row.match_snippet("run")

    assert snippet[match_start : match_start + len("run")] == "run"
    assert snippet[:match_start] == "first "


def test_match_snippet_radius_controls_window_size():
    notes = ("filler text " * 30) + "needle" + (" filler text" * 30)
    row = make_day(notes=notes)

    snippet_default, _ = row.match_snippet("needle")
    snippet_wide, _ = row.match_snippet("needle", radius=200)

    assert len(snippet_wide) > len(snippet_default)


# ------------- Tests for get_all_entries ------------


def seed_days(*days):
    with db_session() as sess:
        for day, notes in days:
            sess.add(make_day(day=day, notes=notes))
        sess.commit()


def test_get_all_entries_orders_by_day_ascending():
    seed_days(
        (date(2026, 3, 1), ""),
        (date(2026, 1, 1), ""),
        (date(2026, 2, 1), ""),
    )

    result = get_all_entries()

    assert [r.day for r in result] == [
        date(2026, 1, 1),
        date(2026, 2, 1),
        date(2026, 3, 1),
    ]


def test_get_all_entries_includes_notes_by_default():
    seed_days((date(2026, 1, 1), "remember this"))

    result = get_all_entries()

    assert "notes" not in inspect(result[0]).unloaded
    assert result[0].notes == "remember this"


def test_get_all_entries_defers_notes_when_include_notes_false():
    seed_days((date(2026, 1, 1), "remember this"))

    result = get_all_entries(include_notes=False)

    assert "notes" in inspect(result[0]).unloaded


# ------------- Tests for get_steps_per_day_from_db ------------


def test_get_steps_per_day_from_db_returns_row_when_day_exists(session):
    session.add(make_day(day=date(2026, 1, 15), step_count=8_421))
    session.commit()

    result = get_steps_per_day_from_db(date(2026, 1, 15), session)

    assert result is not None
    assert result.day == date(2026, 1, 15)
    assert result.step_count == 8_421


def test_get_steps_per_day_from_db_returns_none_when_day_missing(session):
    session.add(make_day(day=date(2026, 1, 15)))
    session.commit()

    result = get_steps_per_day_from_db(date(2026, 1, 16), session)

    assert result is None


# ------------- Tests for DayStats.step_goal_met ------------


@pytest.mark.parametrize(
    "step_count,daily_step_goal,expected",
    [
        (12_000, 10_000, True),  # over goal
        (10_000, 10_000, True),  # exactly at goal
        (8_000, 10_000, False),  # under goal
    ],
)
def test_step_goal_met(step_count, daily_step_goal, expected):
    row = make_day(step_count=step_count, daily_step_goal=daily_step_goal)

    assert row.step_goal_met is expected


# ------------- Tests for DayStats.floors_climbed_goal_met ------------


@pytest.mark.parametrize(
    "floors_climbed,floors_climbed_goal,expected",
    [
        (12, 10, True),  # over goal
        (10, 10, True),  # exactly at goal
        (8, 10, False),  # under goal
        (None, 10, False),  # no floors climbed
        (12, None, False),  # no goal
    ],
)
def test_floors_climbed_goal_met(floors_climbed, floors_climbed_goal, expected):
    row = make_day(
        floors_climbed=floors_climbed, floors_climbed_goal=floors_climbed_goal
    )

    assert row.floors_climbed_goal_met is expected


# ------------- Tests for DayStats.weight_pounds ------------


def test_weight_pounds_converts_grams_to_pounds():
    row = make_day(weight_grams=100_000)

    assert row.weight_pounds == pytest.approx(220.462, rel=1e-4)


def test_weight_pounds_is_none_when_weight_grams_is_none():
    row = make_day(weight_grams=None)

    assert row.weight_pounds is None


# ------------- Tests for DayStats.water_goal_met ------------


@pytest.mark.parametrize(
    "water_consumed_ml,water_goal_ml,expected",
    [
        (2_500, 2_000, True),  # over goal
        (2_000, 2_000, True),  # exactly at goal
        (1_500, 2_000, False),  # under goal
        (None, 2_000, False),  # no water consumed
        (2_500, None, False),  # no goal
    ],
)
def test_water_goal_met(water_consumed_ml, water_goal_ml, expected):
    row = make_day(water_consumed_ml=water_consumed_ml, water_goal_ml=water_goal_ml)

    assert row.water_goal_met is expected
