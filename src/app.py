import atexit
import json
import os
import subprocess
from collections import defaultdict
from datetime import datetime, timedelta
from statistics import mean

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from flask import Flask, jsonify
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
    atexit.register(scheduler.shutdown)


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
        loader=FileSystemLoader(os.path.dirname(os.path.abspath(__file__))),
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
            steps_by_day.get(end_day - timedelta(days=i), 0)
            for i in range(days)
        )

    this_week_steps = _sum_range(yesterday, 7)
    last_week_steps = _sum_range(yesterday - timedelta(days=7), 7)
    four_week_avg = _sum_range(yesterday, 28) // 4 if any(
        steps_by_day.get(yesterday - timedelta(days=i)) for i in range(28)
    ) else 0

    if last_week_steps:
        wow_pct = round((this_week_steps - last_week_steps) / last_week_steps * 100)
    else:
        wow_pct = None

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

    def _build_weekly(lookup, mode="sum"):
        if mode == "avg":
            # Average over days that actually have data (ignores nulls so a
            # missed night doesn't drag down a week's average).
            def _avg(end_day, days):
                vals = [
                    lookup[end_day - timedelta(days=i)]
                    for i in range(days)
                    if lookup.get(end_day - timedelta(days=i))
                ]
                return round(sum(vals) / len(vals)) if vals else 0
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
            "wow_pct": (
                round((this_w - last_w) / last_w * 100) if last_w else None
            ),
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
        e for e in entries
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
    sleep_avg_seconds = (
        round(sum(e.sleep_total_seconds for e in sleep_entries) / sleep_total_days)
        if sleep_total_days else 0
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

    # Sleep score vs sleep duration scatter — last 365 days, only days with both
    sleep_scatter = [
        {"x": round(e.sleep_total_seconds / 3600, 2), "y": e.sleep_score}
        for e in sleep_entries
        if e.day >= cutoff and e.sleep_score is not None
    ]

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
                if sleep_total_days else "—"
            ),
            "sleep_avg_score": sleep_avg_score,
            "sleep_scored_days": len(sleep_scored),
            "hist_summary": hist_summary,
            "weekly_comparison": weekly_comparison,
            "hyd_weekly_comparison": hyd_weekly_comparison,
            "sleep_weekly_comparison": sleep_weekly_comparison,
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
            "sleep_scatter": sleep_scatter,
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
    )


# Triggered by the dashboard's debug-panel "Force update" button. Spawns
# scripts/force-update.sh detached so it survives the systemctl restart
# that kills this process.
FORCE_UPDATE_SCRIPT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "force-update.sh"
)


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
