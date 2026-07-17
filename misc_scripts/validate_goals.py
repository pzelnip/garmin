"""Validate — and where possible auto-fix — goals.json before it is published.

Two entry points:

    python validate_goals.py --check goals.json   # report problems, exit 1 on any
    python validate_goals.py --fix   goals.json   # rewrite in place: rungs sorted
                                                  # newest-first per phase + canonical
                                                  # formatting; exit 1 on unfixable

Also imported by edit_goals.py (the interactive editor loop), which validates
each save before publishing. The only things auto-fixed are rung ordering and
formatting;
anything that needs a human decision — malformed JSON, an unknown status, a date
we can't read, zero or several "current" rungs, or a status that disagrees with
its date — is reported for the user to fix and save again.
"""

import argparse
import json
import re
import sys
from datetime import date, timedelta

# Read top-to-bottom a phase's rungs go newest -> oldest, so statuses must run
# future -> current -> done. Ranks let us assert that ordering cheaply.
STATUS_RANK = {"future": 2, "current": 1, "done": 0}

_MONTHS = {}
for _i, (_full, _ab) in enumerate(
    [
        ("january", "jan"),
        ("february", "feb"),
        ("march", "mar"),
        ("april", "apr"),
        ("may", "may"),
        ("june", "jun"),
        ("july", "jul"),
        ("august", "aug"),
        ("september", "sep"),
        ("october", "oct"),
        ("november", "nov"),
        ("december", "dec"),
    ],
    start=1,
):
    _MONTHS[_full] = _i
    _MONTHS[_ab] = _i
_MONTHS["sept"] = 9

# "Aug 15 / 16, 2026" / "August 18, 2026" / "Jan 2028" — month, optional day
# (first of a "/ 16" range), optional comma, year.
_DATE_RE = re.compile(
    r"^([A-Za-z]+)\s+(?:(\d{1,2})\s*(?:/\s*\d{1,2})?\s*,?\s*)?(\d{4})$"
)
_QUALIFIER = re.compile(r"(?i)^(before|by|after|around|approx\.?|~)\s+")


class GoalsError(Exception):
    """One or more problems a human must fix; str() is a multi-line report."""


def parse_date(raw):
    """Parse one of the ladder's free-form date strings into a `date` for
    sorting. Raises ValueError with a friendly message when it can't."""
    s = str(raw).split("·")[0].strip()  # drop "· physio appt"-style suffixes
    delta = timedelta(0)
    qm = _QUALIFIER.match(s)
    if qm:
        # "Before October 2026" should sort just older than "October 2026".
        delta = (
            timedelta(days=-1)
            if qm.group(1).lower() in ("before", "by")
            else timedelta(days=1)
        )
        s = _QUALIFIER.sub("", s).strip()
    m = _DATE_RE.match(s)
    if not m:
        raise ValueError(f"can't read a date from {raw!r}")
    month = _MONTHS.get(m.group(1).lower())
    if month is None:
        raise ValueError(f"unknown month in {raw!r}")
    day = int(m.group(2)) if m.group(2) else 1
    try:
        return date(int(m.group(3)), month, day) + delta
    except ValueError:
        raise ValueError(f"invalid calendar date in {raw!r}")


def _check_schema(data, errors):
    if not isinstance(data, dict):
        errors.append("the top level must be a JSON object")
        return
    summit = data.get("summit")
    if (
        not isinstance(summit, dict)
        or not summit.get("date")
        or not summit.get("title")
    ):
        errors.append('"summit" must be an object with a "date" and a "title"')
    phases = data.get("phases")
    if not isinstance(phases, list) or not phases:
        errors.append('"phases" must be a non-empty list')
        return
    for pi, ph in enumerate(phases):
        where = f"phase #{pi + 1}"
        if not isinstance(ph, dict) or not ph.get("name"):
            errors.append(f'{where}: needs a "name"')
        rungs = ph.get("rungs") if isinstance(ph, dict) else None
        if not isinstance(rungs, list):
            errors.append(f'{where}: needs a "rungs" list')
            continue
        name = ph.get("name", where)
        for ri, r in enumerate(rungs):
            rwhere = f"{name!r} rung #{ri + 1}"
            if not isinstance(r, dict):
                errors.append(f"{rwhere}: is not an object")
                continue
            for key in ("date", "title", "status"):
                if not r.get(key):
                    errors.append(f'{rwhere}: missing "{key}"')
            status = r.get("status")
            if status and status not in STATUS_RANK:
                errors.append(
                    f"{rwhere}: status {status!r} must be done, current, or future"
                )


def validate_and_fix(data):
    """Return (fixed_data, changes). `fixed_data` has each phase's rungs sorted
    newest-first; `changes` is a human-readable list of what was reordered.
    Raises GoalsError (with every problem it found) if anything can't be
    auto-fixed."""
    errors = []
    _check_schema(data, errors)
    if errors:
        raise GoalsError(_report(errors))

    for ph in data["phases"]:
        for r in ph["rungs"]:
            try:
                r["_sort"] = parse_date(r["date"])
            except ValueError as e:
                errors.append(str(e))
    if errors:
        raise GoalsError(_report(errors))

    changes = []
    for ph in data["phases"]:
        original = list(ph["rungs"])
        ph["rungs"].sort(key=lambda r: r["_sort"], reverse=True)
        if ph["rungs"] != original:
            changes.append(f'reordered "{ph["name"]}" newest-first')

    rungs = [r for ph in data["phases"] for r in ph["rungs"]]
    currents = [r for r in rungs if r["status"] == "current"]
    if not currents:
        errors.append('no rung is marked "current" — exactly one must be')
    elif len(currents) > 1:
        titles = ", ".join(repr(r["title"]) for r in currents)
        errors.append(
            f'{len(currents)} rungs marked "current" ({titles}) — only one is allowed'
        )

    if not errors:
        prev_rank = max(STATUS_RANK.values()) + 1
        for r in rungs:
            rank = STATUS_RANK[r["status"]]
            if rank > prev_rank:
                errors.append(
                    f'"{r["title"]}" ({r["status"]}, {r["date"]}) is out of place — a '
                    "done/current rung is sitting above a newer future one. Check its "
                    "date or its status."
                )
                break
            prev_rank = rank

    for r in rungs:
        r.pop("_sort", None)
    if errors:
        raise GoalsError(_report(errors))
    return data, changes


def _report(errors):
    return "\n".join(f"  • {e}" for e in errors)


def load(path):
    """Read + JSON-parse goals.json, raising GoalsError with line/column on a
    syntax error (the one problem the user must fix by hand)."""
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise GoalsError(
            f"  • not valid JSON: {e.msg} (line {e.lineno}, column {e.colno})"
        )


def dumps(data):
    """Canonical serialization: 2-space indent, unicode kept, trailing newline."""
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", metavar="FILE", help="validate only")
    group.add_argument("--fix", metavar="FILE", help="validate and rewrite in place")
    args = ap.parse_args(argv)
    path = args.check or args.fix
    try:
        with open(path, encoding="utf-8") as fh:
            original = fh.read()
        fixed, changes = validate_and_fix(load(path))
    except GoalsError as e:
        print(f"✗ {path} has problems:\n{e}", file=sys.stderr)
        return 1
    canonical = dumps(fixed)
    if args.check:
        if canonical != original:
            note = "; ".join(changes) if changes else "formatting"
            print(
                f"✗ {path} is not canonical ({note}) — run `make validate-goals`.",
                file=sys.stderr,
            )
            return 1
        print(f"✓ {path} is valid.")
        return 0
    # --fix
    if canonical != original:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(canonical)
        print("↺ auto-fixed: " + "; ".join(changes or ["reformatted"]))
    print(f"✓ {path} is valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
