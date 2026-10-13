# Ideas:
#
# * streak detector, like show top 10 streaks
# * max/min steps in a day (top 10)
# * use rich/textual for prettiness

import contextlib
from datetime import date, datetime, timedelta
from typing import List

from db import DayStats, get_all_entries


class Streak:
    """A consecutive run of days where the daily step goal was met.

    Constructed from an iterable of DayStats entries known to be
    goal-met and contiguous (see `extract_streaks`). Holds the date range
    spanning the run plus the underlying entries.
    """

    start: date
    end: date
    entries: List[DayStats]

    def __init__(self, iterable):
        self.entries = []

        first_item = next(iterable)
        self.end = self.start = first_item.day
        self.entries.append(first_item)
        for item in iterable:
            self.entries.append(item)
            self.end = item.day

    @classmethod
    def extract_streaks(cls, entries):
        runs = []
        prev_day = None
        for entry in entries:
            if not entry.step_goal_met:
                prev_day = None
                continue
            # A missing row (e.g. a day not yet synced) breaks the run too.
            if prev_day is not None and entry.day == prev_day + timedelta(days=1):
                runs[-1].append(entry)
            else:
                runs.append([entry])
            prev_day = entry.day
        streaks = [Streak(iter(run)) for run in runs]

        # On a length tie, prefer the more recent streak (later end date) so a
        # current streak beats an equal-length older one for the last top-N slot.
        streaks.sort(key=lambda x: (len(x.entries), x.end), reverse=True)
        return streaks

    def day_in_streak(self, day):
        return self.start <= day <= self.end

    @property
    def days(self) -> int:
        return len(self.entries)

    def __str__(self) -> str:
        return f"Streak from {self.start} to {self.end} ({self.days} days){' (current streak)' if self.is_current() else ''}"

    def is_current(self, last_synced=None):
        yesterday = datetime.now().date() - timedelta(days=1)
        if self.day_in_streak(yesterday):
            return True
        # Before the morning sync, yesterday has no data yet, so a streak
        # running up to the latest synced day is still alive.
        day_before = yesterday - timedelta(days=1)
        return self.end == day_before and last_synced == day_before


def build_streaks(entries=None):
    if not entries:
        entries = get_all_entries()
    return Streak.extract_streaks(entries)


def find_current_streak(streaks=None, last_synced=None):
    if not streaks:
        streaks = build_streaks()
    current_streak = None
    with contextlib.suppress(StopIteration):
        current_streak = next(s for s in streaks if s.is_current(last_synced))
    return current_streak
