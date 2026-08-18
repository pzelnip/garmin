import json
import logging
import os
import platform
import re
import subprocess
from datetime import datetime, timedelta
from functools import cache
from importlib.metadata import PackageNotFoundError, version

from flask import Flask, Response, jsonify, redirect, render_template, request, url_for
from jinja2 import select_autoescape
from sqlmodel import select

from dashboard_data import get_dashboard_data_cached, invalidate_dashboard_cache
from db import DayStats, Source, StepTarget, _init_db, db_session, get_goals_data

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


# The Goals-tab ladder is data-driven, not baked into the template. The live
# copy lives in the Neon `goals` table (published out-of-band via
# `make edit-goals`) so it can be updated without a code deploy or a committed
# file. The in-repo goals.json is the authoring source + an offline fallback if
# the table is empty / unreachable.
GOALS_PATH = os.path.join(REPO_ROOT, "goals.json")


def _load_goals():
    """Return the goals-ladder structure augmented with progress totals
    (`done` / `total` / `pct`), or None if no source is available.

    Read fresh on every request (deliberately un-cached, so a push to Neon
    shows up on the next refresh). Source order: the Neon `goals` table first,
    then the committed goals.json as a fallback — the DB read is wrapped so a
    transient Neon issue degrades the Goals tab to the bundled copy rather
    than erroring.
    """
    goals = None
    try:
        goals = get_goals_data()
    except Exception:
        goals = None
    if goals is None:
        try:
            with open(GOALS_PATH, encoding="utf-8") as fh:
                goals = json.load(fh)
        except OSError, ValueError:
            return None
    rungs = [r for phase in goals.get("phases", []) for r in phase.get("rungs", [])]
    total = len(rungs) + (1 if goals.get("summit") else 0)
    done = sum(1 for r in rungs if r.get("status") == "done")
    goals["done"] = done
    goals["total"] = total
    goals["pct"] = round(done / total * 100) if total else 0
    return goals


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
        goals=_load_goals(),
        git_sha=git_sha,
        git_commit_date=git_commit_date,
        diagnostics=_diagnostics(),
    )


# Triggered by the dashboard's debug-panel "Force update" button. Spawns
# scripts/force-update.sh detached so it survives the systemctl restart
# that kills this process.
FORCE_UPDATE_SCRIPT = os.path.join(REPO_ROOT, "scripts", "force-update.sh")
FORCE_UPDATE_LOG = os.path.join(REPO_ROOT, "force-update.log")

# Triggered by the dashboard's debug-panel "Sync now" button. Spawns
# scripts/run-sync.sh detached to run garmin.py --auto without a restart.
SYNC_SCRIPT = os.path.join(REPO_ROOT, "scripts", "run-sync.sh")
SYNC_LOG = os.path.join(REPO_ROOT, "run-sync.log")


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


def _bump_markdown_headings(text):
    """Shift every Markdown heading in `text` down one level (## -> ###, etc)
    so a day's own headings (e.g. "## Notables") nest under that day's
    `## YYYY-MM-DD` section in the export rather than colliding with it.
    """
    return re.sub(r"^(#+)(\s)", r"#\1\2", text, flags=re.MULTILINE)


@app.route("/api/notes/export")
def export_notes():
    """Download notes in [start, end] (inclusive) as a Markdown file, one
    `## YYYY-MM-DD` section per day. Days with empty notes are skipped.

    Query: ?start=YYYY-MM-DD&end=YYYY-MM-DD.
    """
    start = _parse_iso_date(request.args.get("start", ""))
    end = _parse_iso_date(request.args.get("end", ""))
    if start is None or end is None:
        return jsonify({"error": "expected start and end as YYYY-MM-DD"}), 400
    if end < start:
        return jsonify({"error": "end must be on or after start"}), 400

    with db_session() as session:
        stmt = (
            select(DayStats)
            .where(DayStats.day >= start)
            .where(DayStats.day <= end)
            .where(DayStats.notes != "")
            .order_by(DayStats.day)
        )
        rows = session.exec(stmt).all()

    lines = [f"# Notes export: {start.isoformat()} to {end.isoformat()}", ""]
    for row in rows:
        lines.append(f"## {row.day.isoformat()}")
        lines.append("")
        lines.append(_bump_markdown_headings(row.notes.strip()))
        lines.append("")
    body = "\n".join(lines).rstrip() + "\n"

    filename = f"notes_{start.isoformat()}_to_{end.isoformat()}.md"
    return Response(
        body,
        mimetype="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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
                "bmi": round(row.bmi, 3) if row.bmi is not None else None,
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


@app.route("/api/step-plan")
def step_plan():
    """Return step targets + actual steps for every day in [start, end].

    Powers the Step Planning calendar. The front-end requests a range that
    reaches ~4 weeks before the visible grid so it has enough history for the
    per-day same-weekday lookback and the weekly-comparison figures. Targets
    come from `steptarget`; actuals come from `DayStats.step_count`. Days
    with neither are simply absent from the response.

    Query: ?start=YYYY-MM-DD&end=YYYY-MM-DD.
    """
    start = _parse_iso_date(request.args.get("start", ""))
    end = _parse_iso_date(request.args.get("end", ""))
    if start is None or end is None:
        return jsonify({"error": "expected start and end as YYYY-MM-DD"}), 400
    if end < start:
        return jsonify({"error": "end must be on or after start"}), 400

    with db_session() as session:
        targets = session.exec(
            select(StepTarget)
            .where(StepTarget.day >= start)
            .where(StepTarget.day <= end)
        )
        target_by_day = {t.day: t.target for t in targets}
        steps = session.exec(
            select(DayStats.day, DayStats.step_count)
            .where(DayStats.day >= start)
            .where(DayStats.day <= end)
        )
        steps_by_day = {day: count for day, count in steps}

    days = sorted(set(target_by_day) | set(steps_by_day))
    return jsonify(
        {
            "days": [
                {
                    "day": d.isoformat(),
                    "target": target_by_day.get(d),
                    "steps": steps_by_day.get(d),
                }
                for d in days
            ]
        }
    )


@app.route("/api/step-plan/<iso_date>", methods=["PUT"])
def set_step_target(iso_date):
    """Set (upsert) the step target for one day. Any day may be edited,
    including past days (so goals can be backfilled). Body: {"target": positive int}.
    """
    target_day = _parse_iso_date(iso_date)
    if target_day is None:
        return jsonify({"error": "expected YYYY-MM-DD"}), 400

    payload = request.get_json(silent=True) or {}
    value = payload.get("target")
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        return jsonify({"error": "target must be a positive integer"}), 400

    with db_session() as session:
        row = session.exec(
            select(StepTarget).where(StepTarget.day == target_day)
        ).first()
        if row is None:
            row = StepTarget(day=target_day, target=value)
        else:
            row.target = value
        session.add(row)
        session.commit()
    return jsonify({"day": iso_date, "target": value, "saved": True})


def _parse_bulk_days(payload):
    """Validate a bulk request's `days` list. Returns (days, error_response).

    Days are de-duplicated and sorted so a repeated date can't produce two
    rows for the same day.
    """
    raw = payload.get("days")
    if not isinstance(raw, list) or not raw:
        return None, (jsonify({"error": "expected a non-empty days list"}), 400)
    days = set()
    for item in raw:
        parsed = _parse_iso_date(item) if isinstance(item, str) else None
        if parsed is None:
            return None, (jsonify({"error": f"bad day: {item!r}"}), 400)
        days.add(parsed)
    return sorted(days), None


@app.route("/api/step-plan", methods=["PUT"])
def set_step_targets_bulk():
    """Set (upsert) the same step target for several days at once.

    Body: {"days": ["YYYY-MM-DD", ...], "target": positive int}. Backs the
    Step Planning tab's multi-select, so an arbitrary (non-consecutive) set
    of days can be given one target in a single round trip.
    """
    payload = request.get_json(silent=True) or {}
    value = payload.get("target")
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        return jsonify({"error": "target must be a positive integer"}), 400
    days, error = _parse_bulk_days(payload)
    if error:
        return error

    with db_session() as session:
        existing = {
            row.day: row
            for row in session.exec(select(StepTarget).where(StepTarget.day.in_(days)))
        }
        for day in days:
            row = existing.get(day)
            if row is None:
                row = StepTarget(day=day, target=value)
            else:
                row.target = value
            session.add(row)
        session.commit()
    return jsonify(
        {
            "days": [d.isoformat() for d in days],
            "target": value,
            "saved": True,
        }
    )


@app.route("/api/step-plan", methods=["DELETE"])
def clear_step_targets_bulk():
    """Clear the step target for several days at once. Body: {"days": [...]}."""
    days, error = _parse_bulk_days(request.get_json(silent=True) or {})
    if error:
        return error

    with db_session() as session:
        rows = session.exec(select(StepTarget).where(StepTarget.day.in_(days))).all()
        for row in rows:
            session.delete(row)
        session.commit()
    return jsonify({"days": [d.isoformat() for d in days], "cleared": True})


@app.route("/api/step-plan/<iso_date>", methods=["DELETE"])
def clear_step_target(iso_date):
    """Clear the step target for one day (any day, including past)."""
    target_day = _parse_iso_date(iso_date)
    if target_day is None:
        return jsonify({"error": "expected YYYY-MM-DD"}), 400

    with db_session() as session:
        row = session.exec(
            select(StepTarget).where(StepTarget.day == target_day)
        ).first()
        if row is not None:
            session.delete(row)
            session.commit()
    return jsonify({"day": iso_date, "cleared": True})


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


@app.route("/api/sync", methods=["POST"])
def sync_now():
    script = os.path.abspath(SYNC_SCRIPT)
    if not os.path.isfile(script):
        return jsonify({"error": f"script not found at {script}"}), 500
    try:
        logging.info(
            f"{datetime.now().isoformat()} — sync triggered, spawning {script}"
        )
        # Fire-and-forget: the Garmin fetch can take a while, so don't block
        # the request on it. Capture output to a log for post-mortem.
        logfile = open(SYNC_LOG, "a")
        logfile.write(f"\n=== {datetime.now().isoformat()} sync ===\n")
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
