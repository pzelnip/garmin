"""Unit tests for the goals.json validator / auto-fixer.

validate_goals lives in misc_scripts (alongside push_goals) and only depends on
the stdlib, so we put its directory on sys.path rather than relying on pytest's
src-only pythonpath."""

import copy
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "misc_scripts"))

import validate_goals as vg  # noqa: E402


def _ladder(rungs, summit=None):
    return {
        "summit": summit or {"date": "April 2028", "title": "Summit"},
        "phases": [{"name": "Phase", "rungs": copy.deepcopy(rungs)}],
    }


VALID_RUNGS = [
    {"date": "Aug 2027", "title": "later", "status": "future"},
    {"date": "Jul 25, 2026", "title": "now", "status": "current"},
    {"date": "Jul 4, 2026", "title": "earlier", "status": "done"},
]


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Jan 2028", date(2028, 1, 1)),
        ("April 2027", date(2027, 4, 1)),
        ("Aug 31, 2026", date(2026, 8, 31)),
        ("August 18, 2026 · physio appt", date(2026, 8, 18)),
        ("Aug 15 / 16, 2026", date(2026, 8, 15)),
        ("Sept 3, 2026", date(2026, 9, 3)),
        ("Before October 2026", date(2026, 9, 30)),
    ],
)
def test_parse_date(raw, expected):
    assert vg.parse_date(raw) == expected


def test_parse_date_rejects_garbage():
    with pytest.raises(ValueError):
        vg.parse_date("someday soon")


def test_valid_ladder_passes():
    data, changes = vg.validate_and_fix(_ladder(VALID_RUNGS))
    assert changes == []
    # helper sort key must not leak into the published structure
    assert all("_sort" not in r for r in data["phases"][0]["rungs"])


def test_rungs_sorted_newest_first():
    scrambled = [VALID_RUNGS[2], VALID_RUNGS[0], VALID_RUNGS[1]]
    data, changes = vg.validate_and_fix(_ladder(scrambled))
    titles = [r["title"] for r in data["phases"][0]["rungs"]]
    assert titles == ["later", "now", "earlier"]
    assert changes  # reported that it reordered


def test_missing_current_is_error():
    rungs = [dict(r, status="done") for r in VALID_RUNGS]
    with pytest.raises(vg.GoalsError, match="current"):
        vg.validate_and_fix(_ladder(rungs))


def test_multiple_current_is_error():
    rungs = copy.deepcopy(VALID_RUNGS)
    rungs[0]["status"] = "current"
    with pytest.raises(vg.GoalsError, match="only one"):
        vg.validate_and_fix(_ladder(rungs))


def test_status_disagreeing_with_date_is_error():
    # a "done" rung dated newer than the "current" one lands above it after the
    # sort, which is the "current in the wrong spot" mistake we want caught.
    rungs = copy.deepcopy(VALID_RUNGS)
    rungs[2]["date"] = "Dec 2027"  # 'done' but far in the future
    with pytest.raises(vg.GoalsError, match="out of place"):
        vg.validate_and_fix(_ladder(rungs))


def test_unparseable_date_is_error():
    rungs = copy.deepcopy(VALID_RUNGS)
    rungs[0]["date"] = "sometime next year"
    with pytest.raises(vg.GoalsError, match="date"):
        vg.validate_and_fix(_ladder(rungs))


def test_bad_status_enum_is_error():
    rungs = copy.deepcopy(VALID_RUNGS)
    rungs[0]["status"] = "inprogress"
    with pytest.raises(vg.GoalsError, match="done, current, or future"):
        vg.validate_and_fix(_ladder(rungs))


def test_load_reports_json_syntax_error(tmp_path):
    bad = tmp_path / "goals.json"
    bad.write_text('{"summit": ,}', encoding="utf-8")
    with pytest.raises(vg.GoalsError, match="not valid JSON"):
        vg.load(str(bad))


def test_dumps_is_canonical_and_idempotent(tmp_path):
    data, _ = vg.validate_and_fix(_ladder(VALID_RUNGS))
    text = vg.dumps(data)
    assert text.endswith("\n")
    # re-loading + re-validating the canonical output must produce the same bytes
    path = tmp_path / "goals.json"
    path.write_text(text, encoding="utf-8")
    reloaded, changes = vg.validate_and_fix(vg.load(str(path)))
    assert changes == []
    assert vg.dumps(reloaded) == text
