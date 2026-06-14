import sys
from datetime import datetime, timedelta


def last_day():
    today = datetime.now()
    return today - timedelta(days=today.weekday() + 1 if today.weekday() != 0 else 8)


def main():
    print(last_day().strftime("%Y-%m-%d"))


if __name__ == "__main__":
    sys.exit(main())
