import json
import logging
import os
import platform
import subprocess
from datetime import datetime, timedelta
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
    except Exception:
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
FORCE_UPDATE_LOG = os.path.join(REPO_ROOT, "force-update.log")


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

    def process_row(row):
        snippet, match_start = row.match_snippet(query)
        return {
            "day": row.day.isoformat(),
            "snippet": snippet,
            "match_start": match_start,
        }

    query = (request.args.get("q") or "").strip().lower()
    if len(query) < 3:
        return jsonify({"query": query, "results": [], "too_short": True})

    with db_session() as session:
        # ilike for case-insensitive substring match; escape SQL wildcards
        # in the query so a literal % / _ doesn't match unexpectedly.
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = (
            select(DayStats)
            .where(DayStats.notes.ilike(f"%{escaped}%", escape="\\"))
            .order_by(DayStats.day.desc())
        )
        results = [process_row(row) for row in session.exec(stmt)]

    return jsonify({"query": query, "results": results, "match_len": len(query)})


@app.route("/api/day/<iso_date>")
def day_detail(iso_date):
    """Return one day's DayStats fields as JSON for the Day-view tab."""
    target = _parse_iso_date(iso_date)
    if target is None:
        return jsonify({"error": "expected YYYY-MM-DD"}), 400

    today = datetime.now().date()
    is_today = target == today
    with db_session() as session:
        row = session.exec(select(DayStats).where(DayStats.day == target)).first()
        if row is None:
            # Today and yesterday are special: the morning Garmin sync ends at
            # yesterday, so until it runs  both can legitimately lack a row
            # while the user still wants to record notes / mood. Surface
            # `annotatable` so the front-end renders those panels.
            return jsonify(
                {
                    "day": iso_date,
                    "found": False,
                    "is_today": is_today,
                    "annotatable": today - timedelta(days=1) <= target <= today,
                }
            )

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
    doesn't exist *and* the target is today or yesterday. Returns
    (row, created) — or (None, False) otherwise.

    Yesterday is allowed (not just today) so notes / mood can be recorded
    in the window between midnight and the Garmin sync, when the day
    that just ended has no row yet. The stub uses step_count=0 /
    daily_step_goal=0 / source=manual_entry as placeholders; the morning
    sync (which covers the last 7 days, ending yesterday) UPSERTs real
    values onto it, so the stub self-heals.
    """
    row = session.exec(select(DayStats).where(DayStats.day == target)).first()
    if row is not None:
        return row, False
    today = datetime.now().date()
    if not (today - timedelta(days=1) <= target <= today):
        return None, False
    row = DayStats(
        day=target,
        step_count=0,
        daily_step_goal=0,
        source=Source.manual_entry,
    )
    return row, True


@app.route("/api/day/<iso_date>", methods=["PUT"])
def update_day(iso_date):
    """Update the manual fields (`notes` and/or `mood_score`) on a DayStats
    row in a single request. Either field may be omitted; only the supplied
    ones are written, so the same endpoint serves "save notes", "save mood",
    or "save both". Creates a stub row for today/yesterday if none exists yet
    (so entries can be recorded before the Garmin sync has run).

    Body: {"notes": str, "mood_score": 1..10 | null}. A present `mood_score`
    of null clears the score; an absent key leaves the field untouched.
    """
    target = _parse_iso_date(iso_date)
    if target is None:
        return jsonify({"error": "expected YYYY-MM-DD"}), 400

    payload = request.get_json(silent=True) or {}
    has_notes = "notes" in payload
    has_mood = "mood_score" in payload
    if not (has_notes or has_mood):
        return jsonify({"error": "expected notes and/or mood_score"}), 400

    notes = payload.get("notes")
    if has_notes and not isinstance(notes, str):
        return jsonify({"error": "notes must be a string"}), 400

    score = payload.get("mood_score")
    if has_mood and score is not None:
        if not isinstance(score, int) or isinstance(score, bool):
            return jsonify({"error": "mood_score must be an int or null"}), 400
        if not 1 <= score <= 10:
            return jsonify({"error": "mood_score must be between 1 and 10"}), 400

    with db_session() as session:
        row, _ = _get_or_create_day(session, target)
        if row is None:
            return jsonify({"error": "no DayStats row for that day"}), 404
        if has_notes:
            row.notes = notes
        if has_mood:
            row.mood_score = score
        session.add(row)
        session.commit()
        invalidate_dashboard_cache()
        return jsonify(
            {
                "day": iso_date,
                "notes": row.notes,
                "mood_score": row.mood_score,
                "saved": True,
            }
        )


@app.route("/api/force-update", methods=["POST"])
def force_update():
    script = os.path.abspath(FORCE_UPDATE_SCRIPT)
    if not os.path.isfile(script):
        return jsonify({"error": f"script not found at {script}"}), 500
    try:
        # Fire-and-forget: detached so the pull+restart outlives this request.
        # `with` would wait on / close the process, defeating the purpose.
        logging.info(
            f"{datetime.now().isoformat()} — force-update triggered, spawning {script}"
        )
        # Capture the script's output to a log file rather than discarding it:
        # the restart kills this process, so a silent failure (e.g. a sudoers
        # mismatch) would otherwise leave no trace. The fd intentionally stays
        # open for the detached child to inherit.
        logfile = open(FORCE_UPDATE_LOG, "a")
        logfile.write(f"\n=== {datetime.now().isoformat()} force-update ===\n")
        logfile.flush()
        subprocess.Popen(
            ["/usr/bin/env", "bash", script],
            stdout=logfile,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        return jsonify({"started": True})
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


if __name__ == "__main__":
    _init_db()
    app.run(debug=DEBUG, port=9329, host="0.0.0.0")
