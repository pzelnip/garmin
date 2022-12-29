# Ideas:
#
# * streak detector, like show top 10 streaks
# * max/min steps in a day (top 10)
# * use rich/textual for prettiness

from datetime import date, datetime, timedelta
from itertools import groupby
from typing import List

from sqlmodel import select

from db import StepEntry, db_session


class Streak:
    start: date
    end: date
    entries: List[StepEntry]

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
        streaks = []
        for goal_met, group_items in groupby(entries, key=lambda x: x.goal_met):
            if goal_met:
                streak = Streak(group_items)
                streaks.append(streak)

        streaks.sort(key=lambda x: len(x.entries), reverse=True)
        return streaks

    def day_in_streak(self, day):
        return self.start <= day <= self.end

    @property
    def days(self) -> int:
        return len(self.entries)

    def __str__(self) -> str:
        return f"Streak from {self.start} to {self.end} ({self.days} days){' (current streak)' if self.is_current() else ''}"

    def is_current(self):
        yesterday = datetime.now().date() - timedelta(days=1)
        return self.day_in_streak(yesterday)


def main():
    with db_session() as session:
        stmt = select(StepEntry).order_by(StepEntry.day)
        entries = list(session.exec(stmt))

    streaks = Streak.extract_streaks(entries)

    max_steps = sorted(entries, key=lambda x: x.step_count, reverse=True)
    min_steps = sorted(entries, key=lambda x: x.step_count)

    for streak in streaks:
        print(streak)

    top_10("Top 10 step totals:", max_steps)
    top_10("Bottom 10 step totals:", min_steps)


def top_10(prompt, entries):
    print()
    print(prompt)
    print()
    for entry in entries[:10]:
        print(f"{entry.day} - {entry.step_count}")


if __name__ == "__main__":
    exit(main())
