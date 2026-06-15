from datetime import date

import pytest

from db import DayStats, Source, get_day_stats_for_date_range


def add_day(session, day, step_count=10_000):
    session.add(
        DayStats(
            day=day,
            step_count=step_count,
            daily_step_goal=10_000,
            source=Source.garmin,
        )
    )


@pytest.mark.parametrize(
    "step_count,daily_step_goal,expected",
    [
        (12_000, 10_000, True),  # over goal
        (10_000, 10_000, True),  # exactly at goal
        (8_000, 10_000, False),  # under goal
    ],
)
def test_step_goal_met(session, step_count, daily_step_goal, expected):
    session.add(
        DayStats(
            day=date(2023, 1, 1),
            step_count=step_count,
            daily_step_goal=daily_step_goal,
            source=Source.garmin,
        )
    )
    session.commit()

    stored = session.get(DayStats, 1)

    assert stored.step_goal_met is expected


def test_get_day_stats_for_date_range_is_inclusive_of_both_bounds(session):
    add_day(session, date(2023, 1, 10))
    add_day(session, date(2023, 1, 15))
    add_day(session, date(2023, 1, 20))
    session.commit()

    result = get_day_stats_for_date_range(session, date(2023, 1, 10), date(2023, 1, 20))

    assert [r.day for r in result] == [
        date(2023, 1, 10),
        date(2023, 1, 15),
        date(2023, 1, 20),
    ]


def test_get_day_stats_for_date_range_excludes_days_outside_range(session):
    add_day(session, date(2023, 1, 9))
    add_day(session, date(2023, 1, 12))
    add_day(session, date(2023, 1, 21))
    session.commit()

    result = get_day_stats_for_date_range(session, date(2023, 1, 10), date(2023, 1, 20))

    assert [r.day for r in result] == [date(2023, 1, 12)]


def test_get_day_stats_for_date_range_orders_by_day_ascending(session):
    add_day(session, date(2023, 1, 18))
    add_day(session, date(2023, 1, 11))
    add_day(session, date(2023, 1, 14))
    session.commit()

    result = get_day_stats_for_date_range(session, date(2023, 1, 10), date(2023, 1, 20))

    assert [r.day for r in result] == [
        date(2023, 1, 11),
        date(2023, 1, 14),
        date(2023, 1, 18),
    ]


def test_get_day_stats_for_date_range_empty_when_no_match(session):
    add_day(session, date(2023, 1, 1))
    session.commit()

    result = get_day_stats_for_date_range(session, date(2023, 2, 1), date(2023, 2, 28))

    assert not result


# ------------- Tests for DayStats.match_snippet ------------


def make_day_with_notes(notes):
    return DayStats(
        day=date(2026, 1, 1),
        step_count=0,
        daily_step_goal=0,
        notes=notes,
        source=Source.garmin,
    )


def test_match_snippet_returns_full_notes_when_shorter_than_radius():
    row = make_day_with_notes("short note about a run")

    snippet, match_start = row.match_snippet("run")

    assert snippet == "short note about a run"
    assert snippet[match_start : match_start + len("run")] == "run"


def test_match_snippet_lowercases_notes_when_matching():
    row = make_day_with_notes("Went for a RUN today")

    snippet, match_start = row.match_snippet("run")

    assert snippet[match_start : match_start + len("run")] == "RUN"


def test_match_snippet_returns_leading_ellipsis_when_match_far_from_start():
    notes = ("filler text " * 20) + "needle at the end"
    row = make_day_with_notes(notes)

    snippet, match_start = row.match_snippet("needle")

    assert snippet.startswith("…")
    assert not snippet.endswith("…")
    assert snippet[match_start : match_start + len("needle")] == "needle"


def test_match_snippet_returns_trailing_ellipsis_when_match_far_from_end():
    notes = "needle at the start" + (" filler text" * 20)
    row = make_day_with_notes(notes)

    snippet, match_start = row.match_snippet("needle")

    assert not snippet.startswith("…")
    assert snippet.endswith("…")
    assert snippet[match_start : match_start + len("needle")] == "needle"


def test_match_snippet_wraps_match_in_both_ellipses_when_far_from_both_ends():
    notes = ("filler text " * 20) + "needle" + (" filler text" * 20)
    row = make_day_with_notes(notes)

    snippet, match_start = row.match_snippet("needle")

    assert snippet.startswith("…")
    assert snippet.endswith("…")
    assert snippet[match_start : match_start + len("needle")] == "needle"
    assert len(snippet) < len(notes)


def test_match_snippet_uses_first_match_when_query_appears_multiple_times():
    row = make_day_with_notes("first run, then another run later")

    snippet, match_start = row.match_snippet("run")

    assert snippet[match_start : match_start + len("run")] == "run"
    assert snippet[:match_start] == "first "


def test_match_snippet_radius_controls_window_size():
    notes = ("filler text " * 30) + "needle" + (" filler text" * 30)
    row = make_day_with_notes(notes)

    snippet_default, _ = row.match_snippet("needle")
    snippet_wide, _ = row.match_snippet("needle", radius=200)

    assert len(snippet_wide) > len(snippet_default)
