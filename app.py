import atexit
import json
import os
from collections import defaultdict
from datetime import datetime, timedelta
from statistics import mean

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from flask import Flask
from jinja2 import Environment, FileSystemLoader, select_autoescape

from sqlmodel import select

from analytics import build_streaks, find_current_streak
from db import StepsToday, _init_db, db_session, get_all_entries
from so_far_today import retrieve_steps_at_hour

DEBUG = True

app = Flask(__name__)


# Start and end hour for the graph
START_HOUR = 9
END_HOUR = 22


# Make sure scheduler is only started once
# See: https://stackoverflow.com/a/25519547/808804
if not DEBUG or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
    scheduler = BackgroundScheduler()
    scheduler.add_job(retrieve_steps_at_hour, CronTrigger.from_crontab("0 9-22 * * *"))
    scheduler.start()
    atexit.register(lambda: scheduler.shutdown())


def none_to_null(iterable):
    """Convert None to 'null' in a list of values."""
    return str(iterable).replace("None", "null")


def get_hourly_steps():
    today = datetime.now().date()
    with db_session() as session:
        stmt = (
            select(StepsToday)
            .where(StepsToday.day == today)
            .where(StepsToday.hour >= START_HOUR)
            .where(StepsToday.hour <= END_HOUR)
            .order_by(StepsToday.hour)
        )

        # Pre-allocate array to allow missing entries
        steps = [None] * (END_HOUR - START_HOUR + 1)
        for entry in session.exec(stmt):
            steps[entry.hour - START_HOUR] = entry.step_count

    return steps


@app.route("/")
def step_progress():
    env = Environment(
        loader=FileSystemLoader("."),
        autoescape=select_autoescape(["html", "xml"]),
    )
    env.filters["none_to_null"] = none_to_null
    return env.get_template("graph.jinja2").render(
        hourly_step_data=get_hourly_steps(),
        start_hour=START_HOUR,
        end_hour=END_HOUR,
    )


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
    dow_avgs = [
        round(mean(dow_buckets[i])) if dow_buckets[i] else 0 for i in range(7)
    ]
    dow_best = dow_names[dow_avgs.index(max(dow_avgs))] if dow_avgs else "—"

    # Time series — last 365 days
    cutoff = datetime.now().date() - timedelta(days=365)
    recent = [e for e in entries if e.day >= cutoff]
    recent_labels = [e.day.isoformat() for e in recent]
    recent_steps = [e.step_count for e in recent]
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

    # Health/biometric trends — filter Nones
    rhr_entries = [(e.day, e.resting_heart_rate) for e in entries if e.resting_heart_rate]
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

    # Calendar heatmap — last 365 days, value = 1 if goal met, 0.5 if some steps, 0 if no data
    heatmap_by_day = {e.day.isoformat(): e for e in recent}
    heatmap = []
    today = datetime.now().date()
    start = today - timedelta(days=364)
    d = start
    while d <= today:
        key = d.isoformat()
        if key in heatmap_by_day:
            e = heatmap_by_day[key]
            level = 4 if e.step_goal_met else min(3, e.step_count // 3000)
        else:
            level = -1
        heatmap.append({"date": key, "weekday": d.weekday(), "level": level})
        d += timedelta(days=1)

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
        },
        "current_streak": (
            {
                "days": current_streak.days,
                "start": current_streak.start.isoformat(),
                "end": current_streak.end.isoformat(),
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
            {"day": e.day.isoformat(), "steps": e.step_count}
            for e in bottom_step_days
        ],
        "charts": {
            "dow": {"labels": dow_names, "values": dow_avgs},
            "recent": {
                "labels": recent_labels,
                "steps": recent_steps,
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
        },
    }


@app.route("/dashboard")
def dashboard():
    data = _build_dashboard_data()
    env = Environment(
        loader=FileSystemLoader("."),
        autoescape=select_autoescape(["html", "xml"]),
    )
    return env.get_template("dashboard.jinja2").render(
        data=data,
        charts_json=json.dumps(data["charts"]),
    )


if __name__ == "__main__":
    _init_db()
    app.run(debug=DEBUG, port=9329)
