import json
import os
import subprocess
from collections import defaultdict
from datetime import datetime, timedelta
from statistics import mean

from flask import Flask, jsonify, request
from jinja2 import Environment, FileSystemLoader, select_autoescape

from sqlmodel import select

from analytics import build_streaks, find_current_streak
from db import DayStats, Source, _init_db, db_session, get_all_entries

# Set GARMIN_DASHBOARD_DEBUG=1 (or any truthy 1/true/yes) to enable Flask's
# reloader / debugger locally. Defaults to False so the Pi runs in
# production mode without needing the var.
DEBUG = os.getenv("GARMIN_DASHBOARD_DEBUG", "").lower() in ("1", "true", "yes")

app = Flask(__name__)


def _rolling_avg(values, window):
    """Return rolling average over the previous `window` values."""
    out = []
    for i in range(len(values)):
        start = max(0, i - window + 1)
        chunk = values[start : i + 1]
        out.append(round(mean(chunk), 1) if chunk else None)
    return out


def _build_dashboard_data():
    entries = get_all_entries()
    streaks = build_streaks(entries)
    current_streak = find_current_streak(streaks)

    total_days = len(entries)
    total_steps = sum(e.step_count for e in entries)
    avg_steps = total_steps // total_days if total_days else 0
    goal_days = sum(1 for e in entries if e.step_goal_met)
    goal_pct = (goal_days / total_days * 100) if total_days else 0
    total_floors = sum(e.floors_climbed or 0 for e in entries)
    total_distance_km = sum(e.distance_traveled_metres or 0 for e in entries) / 1000

    top_step_days = sorted(entries, key=lambda e: e.step_count, reverse=True)[:10]
    bottom_step_days = sorted(entries, key=lambda e: e.step_count)[:10]
    top_streaks = streaks[:10]

    # Day-of-week averages
    dow_buckets = defaultdict(list)
    for e in entries:
        dow_buckets[e.day.weekday()].append(e.step_count)
    dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    dow_avgs = [round(mean(dow_buckets[i])) if dow_buckets[i] else 0 for i in range(7)]
    dow_best = dow_names[dow_avgs.index(max(dow_avgs))] if dow_avgs else "—"

    # Time series — last 365 days (used for floors + activity heatmap)
    cutoff = datetime.now().date() - timedelta(days=365)
    recent = [e for e in entries if e.day >= cutoff]
    # Daily-steps chart shows full history; the panel has a range selector
    # (7d / 30d / 90d / 365d / All) so the front-end slices as needed.
    recent_labels = [e.day.isoformat() for e in entries]
    recent_steps = [e.step_count for e in entries]
    recent_goals = [e.daily_step_goal for e in entries]
    rolling_7 = _rolling_avg(recent_steps, 7)
    rolling_30 = _rolling_avg(recent_steps, 30)

    # Cumulative steps over time (all entries)
    cumulative_labels = [e.day.isoformat() for e in entries]
    cumulative_values = []
    running = 0
    for e in entries:
        running += e.step_count
        cumulative_values.append(running)

    # Monthly totals
    monthly = defaultdict(lambda: {"steps": 0, "days": 0, "goal_days": 0})
    for e in entries:
        key = f"{e.day.year:04d}-{e.day.month:02d}"
        monthly[key]["steps"] += e.step_count
        monthly[key]["days"] += 1
        if e.step_goal_met:
            monthly[key]["goal_days"] += 1
    monthly_labels = sorted(monthly.keys())
    monthly_totals = [monthly[k]["steps"] for k in monthly_labels]
    monthly_avg = [
        round(monthly[k]["steps"] / monthly[k]["days"]) for k in monthly_labels
    ]
    monthly_goal_pct = [
        round(monthly[k]["goal_days"] / monthly[k]["days"] * 100, 1)
        for k in monthly_labels
    ]

    # Weekly comparison — this week / last week / 4-week average. Anchor on
    # "yesterday" so today's partial data doesn't make the current week look
    # artificially low.
    steps_by_day = {e.day: e.step_count for e in entries}
    yesterday = datetime.now().date() - timedelta(days=1)

    def _sum_range(end_day, days):
        return sum(
            steps_by_day.get(end_day - timedelta(days=i), 0) for i in range(days)
        )

    this_week_steps = _sum_range(yesterday, 7)
    last_week_steps = _sum_range(yesterday - timedelta(days=7), 7)
    four_week_avg = (
        _sum_range(yesterday, 28) // 4
        if any(steps_by_day.get(yesterday - timedelta(days=i)) for i in range(28))
        else 0
    )

    if last_week_steps:
        wow_pct = round((this_week_steps - last_week_steps) / last_week_steps * 100)
    else:
        wow_pct = None
    if four_week_avg:
        four_week_pct = round((this_week_steps - four_week_avg) / four_week_avg * 100)
    else:
        four_week_pct = None

    this_week_start = yesterday - timedelta(days=6)
    last_week_start = yesterday - timedelta(days=13)
    last_week_end = yesterday - timedelta(days=7)
    four_week_start = yesterday - timedelta(days=27)
    this_week_range_str = f"{this_week_start:%b %-d} – {yesterday:%b %-d}"
    last_week_range_str = f"{last_week_start:%b %-d} – {last_week_end:%b %-d}"
    four_week_range_str = f"{four_week_start:%b %-d} – {yesterday:%b %-d}"

    weekly_comparison = {
        "this_week": this_week_steps,
        "last_week": last_week_steps,
        "four_week_avg": four_week_avg,
        "wow_pct": wow_pct,
        "four_week_pct": four_week_pct,
        "this_week_range": this_week_range_str,
        "last_week_range": last_week_range_str,
        "four_week_range": four_week_range_str,
    }

    # Same windows for hydration (ml) and sleep (seconds). Both use the same
    # "anchored on yesterday, ignore today" convention as steps. For sleep we
    # average per-night rather than sum (a weekly-total in seconds isn't
    # intuitive), so we expose avg-per-night while still letting the macro
    # render it as h:mm.
    water_by_day = {e.day: e.water_consumed_ml or 0 for e in entries}
    sleep_by_day = {e.day: e.sleep_total_seconds or 0 for e in entries}

    def _sum_lookup(lookup, end_day, days):
        return sum(lookup.get(end_day - timedelta(days=i), 0) for i in range(days))

    def _build_weekly(lookup, mode="sum", precision=0):
        if mode == "avg":
            # Average over days that actually have data (ignores nulls so a
            # missed night doesn't drag down a week's average).
            def _avg(end_day, days):
                vals = [
                    lookup[end_day - timedelta(days=i)]
                    for i in range(days)
                    if lookup.get(end_day - timedelta(days=i))
                ]
                return round(sum(vals) / len(vals), precision) if vals else 0

            this_w = _avg(yesterday, 7)
            last_w = _avg(yesterday - timedelta(days=7), 7)
            four_avg = _avg(yesterday, 28)
        else:
            this_w = _sum_lookup(lookup, yesterday, 7)
            last_w = _sum_lookup(lookup, yesterday - timedelta(days=7), 7)
            four_avg = _sum_lookup(lookup, yesterday, 28) // 4
        return {
            "this_week": this_w,
            "last_week": last_w,
            "four_week_avg": four_avg,
            "wow_pct": (round((this_w - last_w) / last_w * 100) if last_w else None),
            "four_week_pct": (round((this_w - four_avg) / four_avg * 100) if four_avg else None),
            "this_week_range": this_week_range_str,
            "last_week_range": last_week_range_str,
            "four_week_range": four_week_range_str,
        }

    hyd_weekly_comparison = _build_weekly(water_by_day, mode="sum")
    sleep_weekly_comparison = _build_weekly(sleep_by_day, mode="avg")

    # Step distribution histogram (buckets of 2.5k)
    bucket_size = 2500
    hist_buckets = defaultdict(int)
    for e in entries:
        bucket = (e.step_count // bucket_size) * bucket_size
        hist_buckets[bucket] += 1
    if hist_buckets:
        max_bucket = max(hist_buckets.keys())
        hist_labels = [
            f"{b // 1000}k–{(b + bucket_size) // 1000}k"
            for b in range(0, max_bucket + bucket_size, bucket_size)
        ]
        hist_values = [
            hist_buckets.get(b, 0)
            for b in range(0, max_bucket + bucket_size, bucket_size)
        ]
    else:
        hist_labels, hist_values = [], []

    # Step-count summary breakdown — three coarse buckets shown alongside the
    # finer 2.5k-bucket histogram.
    hist_under_5k = sum(1 for e in entries if e.step_count < 5000)
    hist_5k_to_10k = sum(1 for e in entries if 5000 <= e.step_count < 10000)
    hist_over_10k = sum(1 for e in entries if e.step_count >= 10000)
    hist_summary = {
        "under_5k": {
            "count": hist_under_5k,
            "pct": round(hist_under_5k / total_days * 100) if total_days else 0,
        },
        "five_to_ten": {
            "count": hist_5k_to_10k,
            "pct": round(hist_5k_to_10k / total_days * 100) if total_days else 0,
        },
        "over_10k": {
            "count": hist_over_10k,
            "pct": round(hist_over_10k / total_days * 100) if total_days else 0,
        },
    }

    # Health/biometric trends — filter Nones
    rhr_entries = [
        (e.day, e.resting_heart_rate) for e in entries if e.resting_heart_rate
    ]
    rhr_labels = [d.isoformat() for d, _ in rhr_entries]
    rhr_values = [v for _, v in rhr_entries]

    stress_entries = [(e.day, e.stress) for e in entries if e.stress]
    stress_labels = [d.isoformat() for d, _ in stress_entries]
    stress_values = [v for _, v in stress_entries]

    weight_entries = [
        (e.day, round(e.weight_grams * 0.00220462, 1))
        for e in entries
        if e.weight_grams
    ]
    weight_labels = [d.isoformat() for d, _ in weight_entries]
    weight_values = [v for _, v in weight_entries]

    floors_entries = [(e.day, e.floors_climbed or 0) for e in recent]
    floors_labels = [d.isoformat() for d, _ in floors_entries]
    floors_values = [v for _, v in floors_entries]

    # Calendar heatmap — last 365 days, bucketed by absolute step count:
    # -1=no data, 0=0–2.5k, 1=2.5k–5k, 2=5k–7.5k, 3=7.5k–10k, 4=10k+
    heatmap_by_day = {e.day.isoformat(): e for e in recent}
    heatmap = []
    today = datetime.now().date()
    start = today - timedelta(days=364)
    d = start
    while d <= today:
        key = d.isoformat()
        if key in heatmap_by_day:
            steps = heatmap_by_day[key].step_count
            level = min(4, steps // 2500)
        else:
            level = -1
        heatmap.append({"date": key, "weekday": d.weekday(), "level": level})
        d += timedelta(days=1)

    # Hydration — only entries where both consumed and goal are present
    hyd_entries = [
        e
        for e in entries
        if e.water_consumed_ml is not None and e.water_goal_ml is not None
    ]
    hyd_total_days = len(hyd_entries)
    hyd_total_ml = sum(e.water_consumed_ml for e in hyd_entries)
    hyd_avg_ml = round(hyd_total_ml / hyd_total_days) if hyd_total_days else 0
    hyd_goal_days = sum(1 for e in hyd_entries if e.water_goal_met)
    hyd_goal_pct = (hyd_goal_days / hyd_total_days * 100) if hyd_total_days else 0

    # Hydration timeline — last 90 days (hydration is a newer metric, shorter window)
    hyd_cutoff = today - timedelta(days=90)
    hyd_recent = [e for e in hyd_entries if e.day >= hyd_cutoff]
    hyd_timeline_labels = [e.day.isoformat() for e in hyd_recent]
    hyd_timeline_consumed = [e.water_consumed_ml for e in hyd_recent]
    hyd_timeline_goals = [e.water_goal_ml for e in hyd_recent]

    # Hydration by day-of-week
    hyd_dow_buckets = defaultdict(list)
    for e in hyd_entries:
        hyd_dow_buckets[e.day.weekday()].append(e.water_consumed_ml)
    hyd_dow_avgs = [
        round(mean(hyd_dow_buckets[i])) if hyd_dow_buckets[i] else 0 for i in range(7)
    ]

    # Hydration heatmap — last 365 days, level by % of goal
    hyd_by_day = {e.day.isoformat(): e for e in hyd_entries}
    hyd_heatmap = []
    d = start
    while d <= today:
        key = d.isoformat()
        if key in hyd_by_day:
            e = hyd_by_day[key]
            pct = e.water_consumed_ml / e.water_goal_ml if e.water_goal_ml else 0
            if pct >= 1.0:
                level = 4
            elif pct >= 0.75:
                level = 3
            elif pct >= 0.5:
                level = 2
            elif pct > 0:
                level = 1
            else:
                level = 0
        else:
            level = -1
        hyd_heatmap.append({"date": key, "weekday": d.weekday(), "level": level})
        d += timedelta(days=1)

    # Steps vs water scatter — only days with both data points (cap at recent 365 days)
    hyd_scatter = [
        {"x": e.step_count, "y": e.water_consumed_ml}
        for e in hyd_entries
        if e.day >= cutoff
    ]

    # Sleep — entries where total sleep is recorded
    sleep_entries = [e for e in entries if e.sleep_total_seconds is not None]
    sleep_total_days = len(sleep_entries)
    sleep_total_seconds = sum(e.sleep_total_seconds for e in sleep_entries)
    sleep_total_hours = round(sleep_total_seconds / 3600)
    sleep_avg_seconds = (
        round(sleep_total_seconds / sleep_total_days) if sleep_total_days else 0
    )
    sleep_scored = [e for e in sleep_entries if e.sleep_score is not None]
    sleep_avg_score = (
        round(mean(e.sleep_score for e in sleep_scored)) if sleep_scored else 0
    )

    # Sleep timeline — last 90 days
    sleep_cutoff = today - timedelta(days=90)
    sleep_recent = [e for e in sleep_entries if e.day >= sleep_cutoff]
    sleep_timeline_labels = [e.day.isoformat() for e in sleep_recent]
    sleep_timeline_totals = [e.sleep_total_seconds for e in sleep_recent]
    sleep_timeline_scores = [e.sleep_score for e in sleep_recent]
    # Stage breakdown — separate datasets so they can stack
    sleep_stages_deep = [e.sleep_deep_seconds or 0 for e in sleep_recent]
    sleep_stages_light = [e.sleep_light_seconds or 0 for e in sleep_recent]
    sleep_stages_rem = [e.sleep_rem_seconds or 0 for e in sleep_recent]
    sleep_stages_awake = [e.sleep_awake_seconds or 0 for e in sleep_recent]

    # Sleep by day-of-week (avg hours)
    sleep_dow_buckets = defaultdict(list)
    for e in sleep_entries:
        sleep_dow_buckets[e.day.weekday()].append(e.sleep_total_seconds)
    sleep_dow_avgs = [
        round(mean(sleep_dow_buckets[i]) / 3600, 2) if sleep_dow_buckets[i] else 0
        for i in range(7)
    ]

    # Sleep score by day-of-week (avg). Bucket from sleep_scored so days
    # without a score don't drag the average down to 0.
    sleep_score_dow_buckets = defaultdict(list)
    for e in sleep_scored:
        sleep_score_dow_buckets[e.day.weekday()].append(e.sleep_score)
    sleep_score_dow_avgs = [
        round(mean(sleep_score_dow_buckets[i]), 1) if sleep_score_dow_buckets[i] else 0
        for i in range(7)
    ]

    # Sleep score vs sleep duration scatter — last 365 days, only days with both
    sleep_scatter = [
        {"x": round(e.sleep_total_seconds / 3600, 2), "y": e.sleep_score}
        for e in sleep_entries
        if e.day >= cutoff and e.sleep_score is not None
    ]

    # Mood — days where the user recorded a 1-10 score
    mood_entries = [e for e in entries if e.mood_score is not None]
    mood_total_days = len(mood_entries)
    mood_avg = round(mean(e.mood_score for e in mood_entries), 1) if mood_entries else 0
    mood_great_days = sum(1 for e in mood_entries if e.mood_score >= 8)
    mood_rough_days = sum(1 for e in mood_entries if e.mood_score <= 3)

    # Last 30 logged days — feeds the Avg Mood card's sparkline and the
    # "X.X vs previous 30 days" delta (compared against the all-time
    # average so it reads as "recent trend vs your baseline"). Logged
    # days, not calendar days, so a stretch of skipped entries doesn't
    # shrink the window.
    mood_recent_30 = mood_entries[-30:]
    mood_recent_30_avg = (
        round(mean(e.mood_score for e in mood_recent_30), 1) if mood_recent_30 else 0
    )
    mood_30d_delta = (
        round(mood_recent_30_avg - mood_avg, 1) if mood_recent_30 else None
    )
    mood_sparkline = [e.mood_score for e in mood_recent_30]

    # Rough days in the last 14 logged days — feeds the "Key Insights" row.
    mood_recent_14 = mood_entries[-14:]
    mood_recent_14_rough = sum(1 for e in mood_recent_14 if e.mood_score <= 3)
    mood_recent_14_series = [
        {"day": e.day.isoformat(), "score": e.mood_score} for e in mood_recent_14
    ]

    # Mood-by-day lookup feeds the weekly comparison (avg mode skips null days).
    mood_by_day = {e.day: e.mood_score for e in mood_entries}
    mood_weekly_comparison = _build_weekly(mood_by_day, mode="avg", precision=1)

    # Distribution: count per score 1-10, plus a summary bucketed into
    # rough (1-3) / average (4-6) / great (7-10) to mirror the step
    # distribution's under-5k / 5-10k / 10k+ summary row.
    mood_dist_counts = [0] * 10
    for e in mood_entries:
        if 1 <= e.mood_score <= 10:
            mood_dist_counts[e.mood_score - 1] += 1
    mood_rough = sum(mood_dist_counts[0:3])    # scores 1-3
    mood_average = sum(mood_dist_counts[3:6])  # scores 4-6
    mood_great = sum(mood_dist_counts[6:10])   # scores 7-10
    mood_dist_summary = {
        "rough":   {"count": mood_rough,   "pct": round(100 * mood_rough   / mood_total_days) if mood_total_days else 0},
        "average": {"count": mood_average, "pct": round(100 * mood_average / mood_total_days) if mood_total_days else 0},
        "great":   {"count": mood_great,   "pct": round(100 * mood_great   / mood_total_days) if mood_total_days else 0},
    }

    # Mood timeline — full history so the range selector can drill back as far
    # as data exists. Rolling averages are computed over consecutive mood
    # entries (not consecutive calendar days), which fits the sparse cadence
    # better than steps-style calendar-aligned rolling does.
    mood_timeline_labels = [e.day.isoformat() for e in mood_entries]
    mood_timeline_values = [e.mood_score for e in mood_entries]
    mood_rolling_7 = _rolling_avg(mood_timeline_values, 7)
    mood_rolling_30 = _rolling_avg(mood_timeline_values, 30)

    # Mood by day-of-week (avg).
    mood_dow_buckets = defaultdict(list)
    for e in mood_entries:
        mood_dow_buckets[e.day.weekday()].append(e.mood_score)
    mood_dow_avgs = [
        round(mean(mood_dow_buckets[i]), 1) if mood_dow_buckets[i] else 0
        for i in range(7)
    ]
    # Best weekday for mood + delta vs the average of recorded weekdays.
    # Used by the Mood-by-DoW chart footer; None if no mood data exists.
    dow_names_full = ["Monday", "Tuesday", "Wednesday", "Thursday",
                      "Friday", "Saturday", "Sunday"]
    mood_dow_nonzero = [v for v in mood_dow_avgs if v]
    if mood_dow_nonzero:
        best_val = max(mood_dow_nonzero)
        best_idx = mood_dow_avgs.index(best_val)
        weekly_avg = sum(mood_dow_nonzero) / len(mood_dow_nonzero)
        mood_dow_best = {
            "name": dow_names[best_idx],
            "name_full": dow_names_full[best_idx],
            "value": best_val,
            "delta": round(best_val - weekly_avg, 2),
        }
    else:
        mood_dow_best = None

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
            "num_streaks": len(streaks),
            "hyd_total_days": hyd_total_days,
            "hyd_avg_ml": hyd_avg_ml,
            "hyd_goal_days": hyd_goal_days,
            "hyd_goal_pct": hyd_goal_pct,
            "hyd_total_liters": round(hyd_total_ml / 1000, 1),
            "sleep_total_days": sleep_total_days,
            "sleep_avg_hours_str": (
                f"{sleep_avg_seconds // 3600}h {(sleep_avg_seconds % 3600) // 60:02d}m"
                if sleep_total_days
                else "—"
            ),
            "sleep_total_hours": sleep_total_hours,
            "sleep_avg_score": sleep_avg_score,
            "sleep_scored_days": len(sleep_scored),
            "hist_summary": hist_summary,
            "weekly_comparison": weekly_comparison,
            "hyd_weekly_comparison": hyd_weekly_comparison,
            "sleep_weekly_comparison": sleep_weekly_comparison,
            "mood_total_days": mood_total_days,
            "mood_avg": mood_avg,
            "mood_recent_30_avg": mood_recent_30_avg,
            "mood_30d_delta": mood_30d_delta,
            "mood_recent_14_rough": mood_recent_14_rough,
            "mood_great_days": mood_great_days,
            "mood_rough_days": mood_rough_days,
            "mood_weekly_comparison": mood_weekly_comparison,
            "mood_dist_summary": mood_dist_summary,
            "mood_dow_best": mood_dow_best,
        },
        "current_streak": (
            {
                "days": current_streak.days,
                "start": current_streak.start.isoformat(),
                "end": current_streak.end.isoformat(),
                # Most recent prior streak that matched or beat the current
                # one. None if this is the first time, or if it's the
                # longest ever.
                "last_match": next(
                    (
                        {
                            "days": s.days,
                            "start": s.start.isoformat(),
                            "end": s.end.isoformat(),
                        }
                        for s in sorted(streaks, key=lambda x: x.end, reverse=True)
                        if s is not current_streak
                        and s.end < current_streak.start
                        and s.days >= current_streak.days
                    ),
                    None,
                ),
            }
            if current_streak
            else None
        ),
        "top_streaks": [
            {"days": s.days, "start": s.start.isoformat(), "end": s.end.isoformat()}
            for s in top_streaks
        ],
        "top_step_days": [
            {"day": e.day.isoformat(), "steps": e.step_count} for e in top_step_days
        ],
        "bottom_step_days": [
            {"day": e.day.isoformat(), "steps": e.step_count} for e in bottom_step_days
        ],
        "charts": {
            "dow": {"labels": dow_names, "values": dow_avgs},
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
            "histogram": {"labels": hist_labels, "values": hist_values},
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
            "hyd_dow": {"labels": dow_names, "values": hyd_dow_avgs},
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
            "sleep_dow": {"labels": dow_names, "values": sleep_dow_avgs},
            "sleep_score_dow": {"labels": dow_names, "values": sleep_score_dow_avgs},
            "sleep_scatter": sleep_scatter,
            "mood_timeline": {
                "labels": mood_timeline_labels,
                "values": mood_timeline_values,
                "rolling_7": mood_rolling_7,
                "rolling_30": mood_rolling_30,
            },
            "mood_distribution": mood_dist_counts,
            "mood_dow": {"labels": dow_names, "values": mood_dow_avgs},
            "mood_sparkline": mood_sparkline,
            "mood_recent_14": mood_recent_14_series,
            "mood_calendar": [
                {"day": e.day.isoformat(), "score": e.mood_score}
                for e in mood_entries
            ],
        },
    }


def _git_sha():
    repo_root = os.path.abspath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    )
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        )
        return out.stdout.strip()
    except Exception:  # pylint: disable=broad-except
        return None


def _git_commit_date():
    repo_root = os.path.abspath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    )
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%ci", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        )
        raw = out.stdout.strip()
        dt = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S %z")
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:  # pylint: disable=broad-except
        return None


# Server-start timestamp captured at module load so it doesn't drift on every
# /dashboard render. Reset whenever the systemd service restarts.
SERVER_STARTED = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _diagnostics():
    """Collect runtime version/environment info for the debug panel."""
    import platform  # pylint: disable=import-outside-toplevel
    from importlib.metadata import PackageNotFoundError, version  # pylint: disable=import-outside-toplevel

    def _pkg(name):
        try:
            return version(name)
        except PackageNotFoundError:
            return "not installed"

    return {
        "Python": platform.python_version(),
        "Platform": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "Flask": _pkg("flask"),
        "SQLModel": _pkg("sqlmodel"),
        "SQLAlchemy": _pkg("sqlalchemy"),
        "garminconnect": _pkg("garminconnect"),
        "DEBUG mode": "on" if DEBUG else "off",
        "Server started": SERVER_STARTED,
    }


@app.route("/dashboard")
def dashboard():
    data = _build_dashboard_data()
    env = Environment(
        loader=FileSystemLoader(os.path.dirname(os.path.abspath(__file__))),
        autoescape=select_autoescape(["html", "xml"]),
    )
    return env.get_template("dashboard.jinja2").render(
        data=data,
        charts_json=json.dumps(data["charts"]),
        git_sha=_git_sha(),
        git_commit_date=_git_commit_date(),
        diagnostics=_diagnostics(),
    )


# Triggered by the dashboard's debug-panel "Force update" button. Spawns
# scripts/force-update.sh detached so it survives the systemctl restart
# that kills this process.
FORCE_UPDATE_SCRIPT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "force-update.sh"
)


@app.route("/api/day/<iso_date>")
def day_detail(iso_date):
    """Return one day's DayStats fields as JSON for the Day-view tab."""
    try:
        target = datetime.strptime(iso_date, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"error": "expected YYYY-MM-DD"}), 400

    is_today = target == datetime.now().date()
    with db_session() as session:
        row = session.exec(select(DayStats).where(DayStats.day == target)).first()
        if row is None:
            # Today is special: the Garmin sync hasn't necessarily run yet,
            # but the user may still want to record notes / mood — surface
            # is_today so the front-end can render those panels.
            return jsonify({"day": iso_date, "found": False, "is_today": is_today})

        sleep_total = row.sleep_total_seconds
        return jsonify(
            {
                "day": row.day.isoformat(),
                "found": True,
                "is_today": is_today,
                "source": row.source.name if row.source else None,
                "steps": row.step_count,
                "step_goal": row.daily_step_goal,
                "step_goal_met": row.step_goal_met,
                "distance_km": (
                    round(row.distance_traveled_metres / 1000, 2)
                    if row.distance_traveled_metres
                    else None
                ),
                "floors_climbed": row.floors_climbed,
                "floors_descended": row.floors_descended,
                "floors_goal": row.floors_climbed_goal,
                "resting_heart_rate": row.resting_heart_rate,
                "max_heart_rate": row.max_heart_rate,
                "min_heart_rate": row.min_heart_rate,
                "stress": row.stress,
                "max_stress": row.max_stress,
                "weight_grams": row.weight_grams,
                "weight_pounds": (
                    round(row.weight_pounds, 1) if row.weight_grams else None
                ),
                "bmi": row.bmi,
                "body_fat": row.body_fat,
                "body_water": row.body_water,
                "bone_mass": row.bone_mass,
                "muscle_mass": row.muscle_mass,
                "water_consumed_ml": row.water_consumed_ml,
                "water_goal_ml": row.water_goal_ml,
                "water_goal_met": row.water_goal_met,
                "sleep_total_seconds": sleep_total,
                "sleep_total_h": round(sleep_total / 3600, 2) if sleep_total else None,
                "sleep_deep_seconds": row.sleep_deep_seconds,
                "sleep_light_seconds": row.sleep_light_seconds,
                "sleep_rem_seconds": row.sleep_rem_seconds,
                "sleep_awake_seconds": row.sleep_awake_seconds,
                "sleep_score": row.sleep_score,
                "notes": row.notes,
                "mood_score": row.mood_score,
            }
        )


def _get_or_create_day(session, target):
    """Fetch the DayStats row for `target`, creating an empty stub if it
    doesn't exist *and* the target is today. Returns (row, created) — or
    (None, False) if the day doesn't exist and isn't today.

    The stub uses step_count=0 / daily_step_goal=0 / source=manual_entry as
    placeholders; the morning Garmin sync will UPSERT real values onto it.
    """
    row = session.exec(select(DayStats).where(DayStats.day == target)).first()
    if row is not None:
        return row, False
    if target != datetime.now().date():
        return None, False
    row = DayStats(
        day=target,
        step_count=0,
        daily_step_goal=0,
        source=Source.manual_entry,
    )
    return row, True


@app.route("/api/day/<iso_date>/notes", methods=["PUT"])
def update_day_notes(iso_date):
    """Replace the `notes` field on a DayStats row. Creates a stub row for
    today if none exists yet (so the user can record notes before the Garmin
    sync has run)."""
    try:
        target = datetime.strptime(iso_date, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"error": "expected YYYY-MM-DD"}), 400

    payload = request.get_json(silent=True) or {}
    notes = payload.get("notes")
    if not isinstance(notes, str):
        return jsonify({"error": "notes must be a string"}), 400

    with db_session() as session:
        row, _ = _get_or_create_day(session, target)
        if row is None:
            return jsonify({"error": "no DayStats row for that day"}), 404
        row.notes = notes
        session.add(row)
        session.commit()
        return jsonify({"day": iso_date, "notes": notes, "saved": True})


@app.route("/api/day/<iso_date>/mood", methods=["PUT"])
def update_day_mood(iso_date):
    """Set or clear the `mood_score` field on a DayStats row. Creates a stub
    row for today if none exists yet (so the user can record their mood
    before the Garmin sync has run).

    Accepts {"mood_score": 1..10} or {"mood_score": null} (to clear).
    """
    try:
        target = datetime.strptime(iso_date, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"error": "expected YYYY-MM-DD"}), 400

    payload = request.get_json(silent=True) or {}
    score = payload.get("mood_score")
    if score is not None:
        if not isinstance(score, int) or isinstance(score, bool):
            return jsonify({"error": "mood_score must be an int or null"}), 400
        if not 1 <= score <= 10:
            return jsonify({"error": "mood_score must be between 1 and 10"}), 400

    with db_session() as session:
        row, _ = _get_or_create_day(session, target)
        if row is None:
            return jsonify({"error": "no DayStats row for that day"}), 404
        row.mood_score = score
        session.add(row)
        session.commit()
        return jsonify({"day": iso_date, "mood_score": score, "saved": True})


@app.route("/api/force-update", methods=["POST"])
def force_update():
    script = os.path.abspath(FORCE_UPDATE_SCRIPT)
    if not os.path.isfile(script):
        return jsonify({"error": f"script not found at {script}"}), 500
    try:
        subprocess.Popen(
            ["/usr/bin/env", "bash", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return jsonify({"started": True})
    except Exception as ex:  # pylint: disable=broad-except
        return jsonify({"error": str(ex)}), 500


if __name__ == "__main__":
    _init_db()
    app.run(debug=DEBUG, port=9329, host="0.0.0.0")
