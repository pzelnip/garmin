from datetime import date

from freezegun import freeze_time

from analytics import Streak, build_streaks, find_current_streak


class StubEntry:
    """Minimal stand-in for DayStats.

    The streak logic only touches `.day` and `.step_goal_met`, so this avoids
    constructing a full SQLModel row (and any DB) for pure-logic tests.
    """

    def __init__(self, day: date, step_goal_met: bool):
        self.day = day
        self.step_goal_met = step_goal_met


def entries(*specs):
    # specs: (day_of_january_2023, goal_met) tuples
    return [StubEntry(date(2023, 1, d), met) for d, met in specs]


def test_extract_streaks_groups_contiguous_goal_met_days():
    given = entries((1, True), (2, True), (3, True), (4, False), (5, True))

    streaks = Streak.extract_streaks(given)

    assert len(streaks) == 2
    # sorted longest-first
    assert streaks[0].days == 3
    assert streaks[0].start == date(2023, 1, 1)
    assert streaks[0].end == date(2023, 1, 3)
    assert streaks[1].days == 1
    assert streaks[1].start == streaks[1].end == date(2023, 1, 5)


def test_extract_streaks_ignores_unmet_days():
    given = entries((1, False), (2, False))

    streaks = Streak.extract_streaks(given)

    assert streaks == []


def test_extract_streaks_sorts_by_length_descending():
    given = entries(
        (1, True),
        (2, False),
        (3, True),
        (4, True),
        (5, True),
    )

    streaks = Streak.extract_streaks(given)

    assert [s.days for s in streaks] == [3, 1]


def test_day_in_streak_bounds_are_inclusive():
    streak = Streak(iter(entries((10, True), (11, True), (12, True))))

    assert streak.day_in_streak(date(2023, 1, 10)) is True
    assert streak.day_in_streak(date(2023, 1, 12)) is True
    assert streak.day_in_streak(date(2023, 1, 9)) is False
    assert streak.day_in_streak(date(2023, 1, 13)) is False


def test_is_current_true_when_streak_covers_yesterday():
    streak = Streak(iter(entries((13, True), (14, True))))

    with freeze_time(date(2023, 1, 15)):  # yesterday == Jan 14
        assert streak.is_current() is True


def test_is_current_false_when_streak_ended_before_yesterday():
    streak = Streak(iter(entries((1, True), (2, True))))

    with freeze_time(date(2023, 1, 15)):
        assert streak.is_current() is False


def test_build_streaks_uses_supplied_entries_without_db():
    given = entries((1, True), (2, True))

    streaks = build_streaks(given)

    assert len(streaks) == 1
    assert streaks[0].days == 2


def test_find_current_streak_returns_the_active_one():
    given = entries((1, True), (2, False), (13, True), (14, True))
    streaks = build_streaks(given)

    with freeze_time(date(2023, 1, 15)):
        current = find_current_streak(streaks)

    assert current is not None
    assert current.start == date(2023, 1, 13)


def test_find_current_streak_none_when_no_active_streak():
    given = entries((1, True), (2, True))
    streaks = build_streaks(given)

    with freeze_time(date(2023, 1, 15)):
        current = find_current_streak(streaks)

    assert current is None
