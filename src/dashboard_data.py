from collections import defaultdict
from datetime import datetime, timedelta
from heapq import nlargest, nsmallest
from itertools import accumulate
from statistics import mean

from analytics import build_streaks, find_current_streak
from db import get_all_entries

DOW_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DOW_NAMES_FULL = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]
STEP_BUCKET_SIZE = 2500


def rolling_avg(values, window):
    """Return a rolling average in O(n) time using a running total."""
    if not values:
        return []

    out = []
    running_total = 0
    for index, value in enumerate(values):
        running_total += value
        if index >= window:
            running_total -= values[index - window]
            divisor = window
        else:
            divisor = index + 1
        out.append(round(running_total / divisor, 1))
    return out


def _build_dow_averages(entries, value_getter, precision=None):
    buckets = defaultdict(list)
    for entry in entries:
        value = value_getter(entry)
        if value is not None:
            buckets[entry.day.weekday()].append(value)

    if precision is None:
        return [
            round(mean(buckets[index])) if buckets[index] else 0 for index in range(7)
        ]

    return [
        round(mean(buckets[index]), precision) if buckets[index] else 0
        for index in range(7)
    ]


def _build_dow_best(dow_avgs, precision=2):
    """Return the best weekday + its delta vs the average of recorded weekdays.

    Used by the DoW-chart "Best day" footer on every metric page. Returns
    None when there are no non-zero buckets (i.e. no data yet for that
    metric).
    """
    nonzero = [value for value in dow_avgs if value]
    if not nonzero:
        return None
    best_value = max(nonzero)
    best_index = dow_avgs.index(best_value)
    weekly_avg = sum(nonzero) / len(nonzero)
    return {
        "name": DOW_NAMES[best_index],
        "name_full": DOW_NAMES_FULL[best_index],
        "value": best_value,
        "delta": round(best_value - weekly_avg, precision),
    }


def _build_time_series(entries, value_getter, transform=None, *, exclude_zero=False):
    labels = []
    values = []
    for entry in entries:
        value = value_getter(entry)
        if value is None or (exclude_zero and value == 0):
            continue
        labels.append(entry.day.isoformat())
        values.append(transform(value) if transform else value)
    return labels, values


def _sum_lookup(lookup, end_day, days):
    return sum(
        lookup.get(end_day - timedelta(days=offset), 0) for offset in range(days)
    )


def _avg_lookup(lookup, end_day, days, precision=0):
    values = [
        lookup[end_day - timedelta(days=offset)]
        for offset in range(days)
        if lookup.get(end_day - timedelta(days=offset))
    ]
    return round(sum(values) / len(values), precision) if values else 0


def _weekly_ranges(yesterday):
    this_week_start = yesterday - timedelta(days=6)
    last_week_start = yesterday - timedelta(days=13)
    last_week_end = yesterday - timedelta(days=7)
    four_week_start = yesterday - timedelta(days=27)
    return {
        "this_week_range": f"{this_week_start:%b %-d} – {yesterday:%b %-d}",
        "last_week_range": f"{last_week_start:%b %-d} – {last_week_end:%b %-d}",
        "four_week_range": f"{four_week_start:%b %-d} – {yesterday:%b %-d}",
    }


def _build_weekly_comparison(lookup, yesterday, *, mode="sum", precision=0):
    if mode == "avg":
        this_week = _avg_lookup(lookup, yesterday, 7, precision=precision)
        last_week = _avg_lookup(
            lookup, yesterday - timedelta(days=7), 7, precision=precision
        )
        four_week_avg = _avg_lookup(lookup, yesterday, 28, precision=precision)
    else:
        this_week = _sum_lookup(lookup, yesterday, 7)
        last_week = _sum_lookup(lookup, yesterday - timedelta(days=7), 7)
        four_week_avg = _sum_lookup(lookup, yesterday, 28) // 4

    return {
        "this_week": this_week,
        "last_week": last_week,
        "four_week_avg": four_week_avg,
        "wow_pct": (
            round((this_week - last_week) / last_week * 100) if last_week else None
        ),
        "four_week_pct": (
            round((this_week - four_week_avg) / four_week_avg * 100)
            if four_week_avg
            else None
        ),
        **_weekly_ranges(yesterday),
    }


def _build_heatmap(start_day, end_day, values_by_day, level_getter):
    heatmap = []
    current_day = start_day
    while current_day <= end_day:
        value = values_by_day.get(current_day)
        heatmap.append(
            {
                "date": current_day.isoformat(),
                "weekday": current_day.weekday(),
                "level": -1 if value is None else level_getter(value),
            }
        )
        current_day += timedelta(days=1)
    return heatmap


def _hydration_heatmap_level(entry):
    if not entry.water_goal_ml:
        return 0

    pct = entry.water_consumed_ml / entry.water_goal_ml
    if pct >= 1.0:
        return 4
    if pct >= 0.75:
        return 3
    if pct >= 0.5:
        return 2
    if pct > 0:
        return 1
    return 0


def _serialize_step_days(entries):
    return [
        {"day": entry.day.isoformat(), "steps": entry.step_count} for entry in entries
    ]


def _serialize_streak(streak):
    return {
        "days": streak.days,
        "start": streak.start.isoformat(),
        "end": streak.end.isoformat(),
    }


def _build_current_streak_payload(streaks, current_streak):
    if current_streak is None:
        return None

    return {
        **_serialize_streak(current_streak),
        "last_match": next(
            (
                _serialize_streak(streak)
                for streak in sorted(streaks, key=lambda item: item.end, reverse=True)
                if streak is not current_streak
                and streak.end < current_streak.start
                and streak.days >= current_streak.days
            ),
            None,
        ),
    }


def _build_step_histogram(entries, total_days):
    buckets = defaultdict(int)
    for entry in entries:
        bucket = (entry.step_count // STEP_BUCKET_SIZE) * STEP_BUCKET_SIZE
        buckets[bucket] += 1

    if buckets:
        max_bucket = max(buckets)
        bucket_range = range(0, max_bucket + STEP_BUCKET_SIZE, STEP_BUCKET_SIZE)
        labels = [
            f"{bucket // 1000}k–{(bucket + STEP_BUCKET_SIZE) // 1000}k"
            for bucket in bucket_range
        ]
        values = [buckets.get(bucket, 0) for bucket in bucket_range]
    else:
        labels, values = [], []

    under_5k = sum(1 for entry in entries if entry.step_count < 5000)
    five_to_ten = sum(1 for entry in entries if 5000 <= entry.step_count < 10000)
    over_10k = sum(1 for entry in entries if entry.step_count >= 10000)

    return {
        "labels": labels,
        "values": values,
        "summary": {
            "under_5k": {
                "count": under_5k,
                "pct": round(under_5k / total_days * 100) if total_days else 0,
            },
            "five_to_ten": {
                "count": five_to_ten,
                "pct": round(five_to_ten / total_days * 100) if total_days else 0,
            },
            "over_10k": {
                "count": over_10k,
                "pct": round(over_10k / total_days * 100) if total_days else 0,
            },
        },
    }


def build_dashboard_data():
    entries = get_all_entries(include_notes=False)
    streaks = build_streaks(entries)
    current_streak = find_current_streak(streaks)

    total_days = len(entries)
    total_steps = sum(entry.step_count for entry in entries)
    avg_steps = total_steps // total_days if total_days else 0
    goal_days = sum(1 for entry in entries if entry.step_goal_met)
    goal_pct = (goal_days / total_days * 100) if total_days else 0
    total_floors = sum(entry.floors_climbed or 0 for entry in entries)
    total_distance_km = (
        sum(entry.distance_traveled_metres or 0 for entry in entries) / 1000
    )

    top_step_days = nlargest(10, entries, key=lambda entry: entry.step_count)
    bottom_step_days = nsmallest(10, entries, key=lambda entry: entry.step_count)
    top_streaks = streaks[:10]

    dow_avgs = _build_dow_averages(entries, lambda entry: entry.step_count)
    dow_best = DOW_NAMES[dow_avgs.index(max(dow_avgs))] if total_days else "—"
    steps_dow_best = _build_dow_best(dow_avgs, precision=0)

    # Steps headline-panel sparkline + 30d delta (mirrors mood pattern).
    steps_recent_30 = entries[-30:]
    steps_recent_30_avg = (
        round(mean(entry.step_count for entry in steps_recent_30))
        if steps_recent_30 else 0
    )
    steps_30d_delta = (
        round(steps_recent_30_avg - avg_steps) if steps_recent_30 else None
    )
    steps_sparkline = [entry.step_count for entry in steps_recent_30]

    # Goal-met percentage over the last 30 days (insight card).
    steps_recent_30_goal_pct = (
        round(
            sum(1 for entry in steps_recent_30 if entry.step_goal_met)
            / len(steps_recent_30) * 100
        )
        if steps_recent_30 else 0
    )
    # Per-day pass/fail strip for the same window — feeds the dot strips on
    # the Goal-Met Rate and Current Streak insight cards.
    steps_recent_30_goal_strip = [
        bool(entry.step_goal_met) for entry in steps_recent_30
    ]

    today = datetime.now().date()
    yesterday = today - timedelta(days=1)
    last_365_cutoff = today - timedelta(days=365)
    last_90_cutoff = today - timedelta(days=90)
    heatmap_start = today - timedelta(days=364)

    recent_entries = [entry for entry in entries if entry.day >= last_365_cutoff]

    recent_labels = [entry.day.isoformat() for entry in entries]
    recent_steps = [entry.step_count for entry in entries]
    recent_goals = [entry.daily_step_goal for entry in entries]
    rolling_7 = rolling_avg(recent_steps, 7)
    rolling_30 = rolling_avg(recent_steps, 30)

    cumulative_labels = [entry.day.isoformat() for entry in entries]
    cumulative_values = list(accumulate(entry.step_count for entry in entries))

    monthly = defaultdict(lambda: {"steps": 0, "days": 0, "goal_days": 0})
    for entry in entries:
        key = f"{entry.day.year:04d}-{entry.day.month:02d}"
        monthly[key]["steps"] += entry.step_count
        monthly[key]["days"] += 1
        if entry.step_goal_met:
            monthly[key]["goal_days"] += 1

    monthly_labels = sorted(monthly.keys())
    monthly_totals = [monthly[key]["steps"] for key in monthly_labels]
    monthly_avg = [
        round(monthly[key]["steps"] / monthly[key]["days"]) for key in monthly_labels
    ]
    monthly_goal_pct = [
        round(monthly[key]["goal_days"] / monthly[key]["days"] * 100, 1)
        for key in monthly_labels
    ]

    steps_by_day = {entry.day: entry.step_count for entry in entries}
    weekly_comparison = _build_weekly_comparison(steps_by_day, yesterday)

    rhr_labels, rhr_values = _build_time_series(
        entries,
        lambda entry: entry.resting_heart_rate,
        exclude_zero=True,
    )
    stress_labels, stress_values = _build_time_series(
        entries,
        lambda entry: entry.stress,
        exclude_zero=True,
    )
    weight_labels, weight_values = _build_time_series(
        entries,
        lambda entry: entry.weight_grams,
        transform=lambda value: round(value * 0.00220462, 1),
        exclude_zero=True,
    )

    floors_labels = [entry.day.isoformat() for entry in recent_entries]
    floors_values = [entry.floors_climbed or 0 for entry in recent_entries]

    heatmap = _build_heatmap(
        heatmap_start,
        today,
        {entry.day: entry.step_count for entry in recent_entries},
        lambda steps: min(4, steps // STEP_BUCKET_SIZE),
    )

    hyd_entries = [
        entry
        for entry in entries
        if entry.water_consumed_ml is not None and entry.water_goal_ml is not None
    ]
    hyd_total_days = len(hyd_entries)
    hyd_total_ml = sum(entry.water_consumed_ml for entry in hyd_entries)
    hyd_avg_ml = round(hyd_total_ml / hyd_total_days) if hyd_total_days else 0
    hyd_goal_days = sum(1 for entry in hyd_entries if entry.water_goal_met)
    hyd_goal_pct = (hyd_goal_days / hyd_total_days * 100) if hyd_total_days else 0

    hyd_recent = [entry for entry in hyd_entries if entry.day >= last_90_cutoff]
    hyd_timeline_labels = [entry.day.isoformat() for entry in hyd_recent]
    hyd_timeline_consumed = [entry.water_consumed_ml for entry in hyd_recent]
    hyd_timeline_goals = [entry.water_goal_ml for entry in hyd_recent]
    hyd_dow_avgs = _build_dow_averages(
        hyd_entries, lambda entry: entry.water_consumed_ml
    )
    hyd_dow_best = _build_dow_best(hyd_dow_avgs, precision=0)

    # Hydration headline-panel sparkline + 30d delta + goal-met pct insight.
    hyd_recent_30 = hyd_entries[-30:]
    hyd_recent_30_avg = (
        round(mean(entry.water_consumed_ml for entry in hyd_recent_30))
        if hyd_recent_30 else 0
    )
    hyd_30d_delta = (
        round(hyd_recent_30_avg - hyd_avg_ml) if hyd_recent_30 else None
    )
    hyd_sparkline = [entry.water_consumed_ml for entry in hyd_recent_30]
    hyd_recent_30_goal_pct = (
        round(
            sum(1 for entry in hyd_recent_30 if entry.water_goal_met)
            / len(hyd_recent_30) * 100
        )
        if hyd_recent_30 else 0
    )
    hyd_recent_30_goal_strip = [
        bool(entry.water_goal_met) for entry in hyd_recent_30
    ]
    hyd_heatmap = _build_heatmap(
        heatmap_start,
        today,
        {entry.day: entry for entry in hyd_entries},
        _hydration_heatmap_level,
    )
    hyd_scatter = [
        {"x": entry.step_count, "y": entry.water_consumed_ml}
        for entry in hyd_entries
        if entry.day >= last_365_cutoff
    ]
    hyd_weekly_comparison = _build_weekly_comparison(
        {entry.day: entry.water_consumed_ml or 0 for entry in entries},
        yesterday,
    )

    sleep_entries = [
        entry for entry in entries if entry.sleep_total_seconds is not None
    ]
    sleep_total_days = len(sleep_entries)
    sleep_total_seconds = sum(entry.sleep_total_seconds for entry in sleep_entries)
    sleep_total_hours = round(sleep_total_seconds / 3600)
    sleep_avg_seconds = (
        round(sleep_total_seconds / sleep_total_days) if sleep_total_days else 0
    )
    sleep_scored = [entry for entry in sleep_entries if entry.sleep_score is not None]
    sleep_avg_score = (
        round(mean(entry.sleep_score for entry in sleep_scored)) if sleep_scored else 0
    )

    sleep_recent = [entry for entry in sleep_entries if entry.day >= last_90_cutoff]
    sleep_timeline_labels = [entry.day.isoformat() for entry in sleep_recent]
    sleep_timeline_totals = [entry.sleep_total_seconds for entry in sleep_recent]
    sleep_timeline_scores = [entry.sleep_score for entry in sleep_recent]
    sleep_stages_deep = [entry.sleep_deep_seconds or 0 for entry in sleep_recent]
    sleep_stages_light = [entry.sleep_light_seconds or 0 for entry in sleep_recent]
    sleep_stages_rem = [entry.sleep_rem_seconds or 0 for entry in sleep_recent]
    sleep_stages_awake = [entry.sleep_awake_seconds or 0 for entry in sleep_recent]
    sleep_dow_avgs = _build_dow_averages(
        sleep_entries,
        lambda entry: entry.sleep_total_seconds / 3600,
        precision=2,
    )
    sleep_dow_best = _build_dow_best(sleep_dow_avgs, precision=2)
    sleep_score_dow_avgs = _build_dow_averages(
        sleep_scored, lambda entry: entry.sleep_score, precision=1
    )
    sleep_score_dow_best = _build_dow_best(sleep_score_dow_avgs, precision=1)

    # Sleep headline-panel sparkline (last 30 nights, in hours) + 30d
    # score-delta vs all-time avg score (drives the trend insight). Short-night
    # count in the last 14 nights drives the third insight card.
    sleep_recent_30 = sleep_entries[-30:]
    sleep_sparkline = [
        round(entry.sleep_total_seconds / 3600, 2) for entry in sleep_recent_30
    ]
    sleep_recent_30_scored = [
        entry for entry in sleep_recent_30 if entry.sleep_score is not None
    ]
    sleep_recent_30_score_avg = (
        round(mean(entry.sleep_score for entry in sleep_recent_30_scored))
        if sleep_recent_30_scored else 0
    )
    sleep_score_30d_delta = (
        round(sleep_recent_30_score_avg - sleep_avg_score)
        if sleep_recent_30_scored else None
    )
    sleep_recent_14 = sleep_entries[-14:]
    sleep_recent_14_short = sum(
        1 for entry in sleep_recent_14 if entry.sleep_total_seconds < 6 * 3600
    )
    # Per-night strip — "pass" = ≥6h, "fail" = <6h. Feeds the Short Nights
    # insight card's dot strip.
    sleep_recent_14_long_enough = [
        entry.sleep_total_seconds >= 6 * 3600 for entry in sleep_recent_14
    ]
    # Score sparkline for the Avg Sleep Score insight card (last 30 scored
    # nights). Filtered to scored nights so a stretch of unscored days
    # doesn't leave the sparkline empty.
    sleep_score_sparkline = [
        entry.sleep_score
        for entry in sleep_entries[-60:]
        if entry.sleep_score is not None
    ][-30:]
    sleep_scatter = [
        {"x": round(entry.sleep_total_seconds / 3600, 2), "y": entry.sleep_score}
        for entry in sleep_entries
        if entry.day >= last_365_cutoff and entry.sleep_score is not None
    ]
    sleep_weekly_comparison = _build_weekly_comparison(
        {entry.day: entry.sleep_total_seconds or 0 for entry in entries},
        yesterday,
        mode="avg",
    )

    mood_entries = [entry for entry in entries if entry.mood_score is not None]
    mood_total_days = len(mood_entries)
    mood_avg = (
        round(mean(entry.mood_score for entry in mood_entries), 1)
        if mood_entries
        else 0
    )
    mood_recent_30 = mood_entries[-30:]
    mood_30d_delta = (
        round(
            round(mean(entry.mood_score for entry in mood_recent_30), 1) - mood_avg, 1
        )
        if mood_recent_30
        else None
    )
    mood_sparkline = [entry.mood_score for entry in mood_recent_30]

    mood_recent_14 = mood_entries[-14:]
    mood_recent_14_rough = sum(1 for entry in mood_recent_14 if entry.mood_score <= 3)
    mood_recent_14_series = [
        {"day": entry.day.isoformat(), "score": entry.mood_score}
        for entry in mood_recent_14
    ]

    mood_weekly_comparison = _build_weekly_comparison(
        {entry.day: entry.mood_score for entry in mood_entries},
        yesterday,
        mode="avg",
        precision=1,
    )

    mood_dist_counts = [0] * 10
    for entry in mood_entries:
        if 1 <= entry.mood_score <= 10:
            mood_dist_counts[entry.mood_score - 1] += 1

    mood_rough = sum(mood_dist_counts[0:3])
    mood_average = sum(mood_dist_counts[3:6])
    mood_great = sum(mood_dist_counts[6:10])
    mood_dist_summary = {
        "rough": {
            "count": mood_rough,
            "pct": round(100 * mood_rough / mood_total_days) if mood_total_days else 0,
        },
        "average": {
            "count": mood_average,
            "pct": (
                round(100 * mood_average / mood_total_days) if mood_total_days else 0
            ),
        },
        "great": {
            "count": mood_great,
            "pct": round(100 * mood_great / mood_total_days) if mood_total_days else 0,
        },
    }

    mood_timeline_labels = [entry.day.isoformat() for entry in mood_entries]
    mood_timeline_values = [entry.mood_score for entry in mood_entries]
    mood_rolling_7 = rolling_avg(mood_timeline_values, 7)
    mood_rolling_30 = rolling_avg(mood_timeline_values, 30)
    mood_dow_avgs = _build_dow_averages(
        mood_entries, lambda entry: entry.mood_score, precision=1
    )
    mood_dow_best = _build_dow_best(mood_dow_avgs, precision=2)

    step_histogram = _build_step_histogram(entries, total_days)

    return {
        "stats": {
            "total_days": total_days,
            "total_steps": total_steps,
            "avg_steps": avg_steps,
            "goal_days": goal_days,
            "goal_pct": goal_pct,
            "total_floors": int(total_floors),
            "total_distance_km": round(total_distance_km, 1),
            "dow_best": dow_best,
            "steps_dow_best": steps_dow_best,
            "steps_30d_delta": steps_30d_delta,
            "steps_recent_30_goal_pct": steps_recent_30_goal_pct,
            "steps_recent_30_goal_strip": steps_recent_30_goal_strip,
            "num_streaks": len(streaks),
            "hyd_total_days": hyd_total_days,
            "hyd_avg_ml": hyd_avg_ml,
            "hyd_goal_days": hyd_goal_days,
            "hyd_goal_pct": hyd_goal_pct,
            "hyd_total_liters": round(hyd_total_ml / 1000, 1),
            "hyd_dow_best": hyd_dow_best,
            "hyd_30d_delta": hyd_30d_delta,
            "hyd_recent_30_goal_pct": hyd_recent_30_goal_pct,
            "hyd_recent_30_goal_strip": hyd_recent_30_goal_strip,
            "sleep_total_days": sleep_total_days,
            "sleep_avg_hours_str": (
                f"{sleep_avg_seconds // 3600}h {(sleep_avg_seconds % 3600) // 60:02d}m"
                if sleep_total_days
                else "—"
            ),
            "sleep_total_hours": sleep_total_hours,
            "sleep_avg_score": sleep_avg_score,
            "sleep_scored_days": len(sleep_scored),
            "sleep_dow_best": sleep_dow_best,
            "sleep_score_dow_best": sleep_score_dow_best,
            "sleep_score_30d_delta": sleep_score_30d_delta,
            "sleep_recent_14_short": sleep_recent_14_short,
            "sleep_recent_14_long_enough": sleep_recent_14_long_enough,
            "hist_summary": step_histogram["summary"],
            "weekly_comparison": weekly_comparison,
            "hyd_weekly_comparison": hyd_weekly_comparison,
            "sleep_weekly_comparison": sleep_weekly_comparison,
            "mood_total_days": mood_total_days,
            "mood_avg": mood_avg,
            "mood_30d_delta": mood_30d_delta,
            "mood_recent_14_rough": mood_recent_14_rough,
            "mood_weekly_comparison": mood_weekly_comparison,
            "mood_dist_summary": mood_dist_summary,
            "mood_dow_best": mood_dow_best,
        },
        "current_streak": _build_current_streak_payload(streaks, current_streak),
        "top_streaks": [_serialize_streak(streak) for streak in top_streaks],
        "top_step_days": _serialize_step_days(top_step_days),
        "bottom_step_days": _serialize_step_days(bottom_step_days),
        "charts": {
            "dow": {"labels": DOW_NAMES, "values": dow_avgs},
            "recent": {
                "labels": recent_labels,
                "steps": recent_steps,
                "goals": recent_goals,
                "rolling_7": rolling_7,
                "rolling_30": rolling_30,
            },
            "cumulative": {"labels": cumulative_labels, "values": cumulative_values},
            "monthly": {
                "labels": monthly_labels,
                "totals": monthly_totals,
                "avg": monthly_avg,
                "goal_pct": monthly_goal_pct,
            },
            "histogram": {
                "labels": step_histogram["labels"],
                "values": step_histogram["values"],
            },
            "rhr": {"labels": rhr_labels, "values": rhr_values},
            "stress": {"labels": stress_labels, "values": stress_values},
            "weight": {"labels": weight_labels, "values": weight_values},
            "floors": {"labels": floors_labels, "values": floors_values},
            "heatmap": heatmap,
            "hyd_timeline": {
                "labels": hyd_timeline_labels,
                "consumed": hyd_timeline_consumed,
                "goals": hyd_timeline_goals,
            },
            "hyd_dow": {"labels": DOW_NAMES, "values": hyd_dow_avgs},
            "hyd_heatmap": hyd_heatmap,
            "hyd_scatter": hyd_scatter,
            "sleep_timeline": {
                "labels": sleep_timeline_labels,
                "totals": sleep_timeline_totals,
                "scores": sleep_timeline_scores,
                "deep": sleep_stages_deep,
                "light": sleep_stages_light,
                "rem": sleep_stages_rem,
                "awake": sleep_stages_awake,
            },
            "sleep_dow": {"labels": DOW_NAMES, "values": sleep_dow_avgs},
            "sleep_score_dow": {"labels": DOW_NAMES, "values": sleep_score_dow_avgs},
            "sleep_scatter": sleep_scatter,
            "mood_timeline": {
                "labels": mood_timeline_labels,
                "values": mood_timeline_values,
                "rolling_7": mood_rolling_7,
                "rolling_30": mood_rolling_30,
            },
            "mood_distribution": mood_dist_counts,
            "mood_dow": {"labels": DOW_NAMES, "values": mood_dow_avgs},
            "mood_sparkline": mood_sparkline,
            "steps_sparkline": steps_sparkline,
            "hyd_sparkline": hyd_sparkline,
            "sleep_sparkline": sleep_sparkline,
            "sleep_score_sparkline": sleep_score_sparkline,
            "mood_recent_14": mood_recent_14_series,
            "mood_calendar": [
                {"day": entry.day.isoformat(), "score": entry.mood_score}
                for entry in mood_entries
            ],
        },
    }
