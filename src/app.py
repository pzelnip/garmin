import json
import os
import platform
import subprocess
from datetime import datetime
from functools import cache
from importlib.metadata import PackageNotFoundError, version

from flask import Flask, jsonify, redirect, render_template, request, url_for
from jinja2 import select_autoescape
from sqlmodel import select

from dashboard_data import get_dashboard_data_cached, invalidate_dashboard_cache
from db import DayStats, Source, _init_db, db_session

# Set GARMIN_DASHBOARD_DEBUG=1 (or any truthy 1/true/yes) to enable Flask's
# reloader / debugger locally. Defaults to False so the Pi runs in
# production mode without needing the var.
DEBUG = os.getenv("GARMIN_DASHBOARD_DEBUG", "").lower() in ("1", "true", "yes")

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(APP_ROOT, ".."))
app = Flask(__name__, template_folder=APP_ROOT)
# Keep `.jinja2` templates autoescaped to match the old custom Jinja
# environment rather than Flask's looser extension-based default.
app.jinja_env.autoescape = select_autoescape(
    enabled_extensions=("html", "htm", "xml", "jinja2"),
    default_for_string=True,
)


@cache
def _git_info():
    # Cached for the process lifetime. Production restarts after each pull, while
    # local dev may briefly show a stale commit until the server restarts.
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%h\t%ci", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        )
        raw_sha, raw_date = out.stdout.strip().split("\t", maxsplit=1)
        commit_date = datetime.strptime(raw_date, "%Y-%m-%d %H:%M:%S %z")
        return raw_sha, commit_date.strftime("%Y-%m-%d %H:%M")
    except Exception:  # pylint: disable=broad-except
        return None, None


# Server-start timestamp captured at module load so it doesn't drift on every
# /dashboard render. Reset whenever the systemd service restarts.
SERVER_STARTED = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@cache
def _diagnostics():
    """Collect runtime version/environment info for the debug panel."""
    # Like `_git_info`, this is process-lifetime data in production and only
    # potentially stale during long-lived local debug sessions.

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


@app.route("/")
def index():
    return redirect(url_for("dashboard"))


@app.route("/dashboard")
def dashboard():
    data = get_dashboard_data_cached()
    git_sha, git_commit_date = _git_info()
    return render_template(
        "dashboard.jinja2",
        data=data,
        charts_json=json.dumps(data["charts"]),
        git_sha=git_sha,
        git_commit_date=git_commit_date,
        diagnostics=_diagnostics(),
    )


# Triggered by the dashboard's debug-panel "Force update" button. Spawns
# scripts/force-update.sh detached so it survives the systemctl restart
# that kills this process.
FORCE_UPDATE_SCRIPT = os.path.join(REPO_ROOT, "scripts", "force-update.sh")


def _parse_iso_date(iso_date):
    try:
        return datetime.strptime(iso_date, "%Y-%m-%d").date()
    except ValueError:
        return None


@app.route("/api/notes/search")
def search_notes():
    """Case-insensitive substring search across the notes field. Returns
    matching days (newest first) with a small snippet around the match.
    """
    query = (request.args.get("q") or "").strip()
    if not query:
        return jsonify({"query": "", "results": []})

    with db_session() as session:
        # ilike for case-insensitive substring match; escape SQL wildcards
        # in the query so a literal % / _ doesn't match unexpectedly.
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = (
            select(DayStats)
            .where(DayStats.notes.ilike(f"%{escaped}%", escape="\\"))
            .order_by(DayStats.day.desc())
        )
        rows = list(session.exec(stmt))

    lower_query = query.lower()
    snippet_radius = 60
    results = []
    for row in rows:
        idx = row.notes.lower().find(lower_query)
        if idx < 0:
            continue
        start = max(0, idx - snippet_radius)
        end = min(len(row.notes), idx + len(query) + snippet_radius)
        snippet = row.notes[start:end]
        if start > 0:
            snippet = "…" + snippet
        if end < len(row.notes):
            snippet = snippet + "…"
        results.append(
            {
                "day": row.day.isoformat(),
                "snippet": snippet,
                "match_start": idx - start + (1 if start > 0 else 0),
                "match_len": len(query),
            }
        )
    return jsonify({"query": query, "results": results})


@app.route("/api/day/<iso_date>")
def day_detail(iso_date):
    """Return one day's DayStats fields as JSON for the Day-view tab."""
    target = _parse_iso_date(iso_date)
    if target is None:
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
    target = _parse_iso_date(iso_date)
    if target is None:
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
        invalidate_dashboard_cache()
        return jsonify({"day": iso_date, "notes": notes, "saved": True})


@app.route("/api/day/<iso_date>/mood", methods=["PUT"])
def update_day_mood(iso_date):
    """Set or clear the `mood_score` field on a DayStats row. Creates a stub
    row for today if none exists yet (so the user can record their mood
    before the Garmin sync has run).

    Accepts {"mood_score": 1..10} or {"mood_score": null} (to clear).
    """
    target = _parse_iso_date(iso_date)
    if target is None:
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
        invalidate_dashboard_cache()
        return jsonify({"day": iso_date, "mood_score": score, "saved": True})


@app.route("/api/force-update", methods=["POST"])
def force_update():
    script = os.path.abspath(FORCE_UPDATE_SCRIPT)
    if not os.path.isfile(script):
        return jsonify({"error": f"script not found at {script}"}), 500
    try:
        # Fire-and-forget: detached so the pull+restart outlives this request.
        # `with` would wait on / close the process, defeating the purpose.
        subprocess.Popen(  # pylint: disable=consider-using-with
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
