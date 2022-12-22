from calendar import monthrange
from datetime import date, datetime
from enum import Enum, auto

from bullet import Bullet, ScrollBar, colors
from bullet import Bullet, Numbers


def date_picker(number_of_years=15):
    class State(Enum):
        PICK_YEAR = auto()
        PICK_MONTH = auto()
        PICK_DAY = auto()
        DONE = auto()

    BACK_STRING = "... previous"

    def scrollbar(prompt, options, return_index=False, allow_previous=True):
        print("\n")

        result = ScrollBar(
            f"{prompt}: ",
            options + [BACK_STRING] if allow_previous else options,
            height=5,
            align=5,
            margin=0,
            pointer="👉 ",
            background_on_switch=colors.background["default"],
            word_on_switch=colors.foreground["default"],
            return_index=True,
        ).launch()

        # if picked BACK_STRING return None
        if result[1] == len(options):
            return None

        # Otherwise int the result
        return int(result[1]) if return_index else int(result[0])

    def pick_year():
        current_year = datetime.now().year

        years = [
            str(i) for i in range(current_year, current_year - number_of_years, -1)
        ]
        return scrollbar("Year", years, allow_previous=False)

    def pick_month():
        months = "January February March April May June July August September October November December".split()
        result = scrollbar("Month", months, return_index=True)
        return result if result is None else result + 1

    def pick_day(year, month):
        max_days = monthrange(year, month)[1]
        days = [str(i) for i in range(1, max_days + 1)]
        return scrollbar("Day", days)

    state = State.PICK_YEAR
    while state != State.DONE:
        if state == State.PICK_YEAR:
            year = pick_year()
            state = State.PICK_MONTH
        elif state == State.PICK_MONTH:
            month = pick_month()
            state = State.PICK_DAY if month else State.PICK_YEAR
        elif state == State.PICK_DAY:
            day = pick_day(year, month)
            state = State.DONE if day else State.PICK_MONTH

    return date(year, month, day)


def yes_no(prompt, default):
    return (
        Bullet(
            prompt=f"\n{prompt} ",
            choices=["No", "Yes"],
            align=5,
            margin=2,
            bullet="",
            pad_right=5,
        ).launch(default=default)
        == "Yes"
    )


def number_picker(prompt, default):
    return Numbers(f"{prompt} (default {default}) ", type=int).launch(default=default)
