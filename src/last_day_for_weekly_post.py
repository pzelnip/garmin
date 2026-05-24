from datetime import datetime, date, timedelta

import pytest
from freezegun import freeze_time


def last_day():
    today = datetime.now()
    return today - timedelta(days=today.weekday() + 1 if today.weekday() != 0 else 8)


@pytest.mark.parametrize(
    "today,expected_date",
    [
        (datetime(2023, 1, 30), date(2023, 1, 22)),
        (datetime(2023, 1, 29), date(2023, 1, 22)),
        (datetime(2023, 1, 28), date(2023, 1, 22)),
        (datetime(2023, 1, 27), date(2023, 1, 22)),
        (datetime(2023, 1, 26), date(2023, 1, 22)),
        (datetime(2023, 1, 25), date(2023, 1, 22)),
        (datetime(2023, 1, 24), date(2023, 1, 22)),
        (datetime(2023, 1, 23), date(2023, 1, 15)),
    ],
)
def test_last_day(today, expected_date):
    with freeze_time(today):
        result = last_day()

    assert result.date() == expected_date


def main():
    print(last_day().strftime("%Y-%m-%d"))


if __name__ == "__main__":
    exit(main())
