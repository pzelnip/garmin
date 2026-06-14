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
