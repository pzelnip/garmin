"""Phase 1 dashboard UI tests: render /dashboard against a seeded in-memory DB
and assert the data -> template -> page contract holds. No browser / no JS
execution -- the inline JS is Phase 2 (Playwright). See the testing-strategy
plan for the full picture."""

import json
import re
from datetime import date, timedelta

import pytest
from bs4 import BeautifulSoup
from helpers import add_day

import dashboard_data
import db

TAB_NAMES = ["steps", "water", "sleep", "mood", "day", "goals"]

# Top-level keys the template's `const charts = {...}` blob must expose. If
# dashboard_data.py renames one, the JS silently breaks -- this list pins it.
EXPECTED_CHART_KEYS = {
    "dow",
    "recent",
    "cumulative",
    "monthly",
    "histogram",
    "hyd_timeline",
    "hyd_dow",
    "hyd_heatmap",
    "hyd_scatter",
    "sleep_timeline",
    "sleep_dow",
    "sleep_score_dow",
    "sleep_scatter",
    "mood_timeline",
    "mood_distribution",
    "mood_dow",
    "mood_calendar",
}


@pytest.fixture(autouse=True)
def fresh_dashboard_cache():
    """build_dashboard_data() is wrapped in a 5-minute process-local cache.
    Drop it before and after each test so a render never serves another
    test's seeded data."""
    dashboard_data.invalidate_dashboard_cache()
    yield
    dashboard_data.invalidate_dashboard_cache()


def seed_representative_week():
    """Seed two weeks ending yesterday with steps, water, sleep, and mood so
    every chart array is non-empty. Returns the rows for value assertions."""
    yesterday = date.today() - timedelta(days=1)
    rows = []
    for offset in range(14, 0, -1):
        day = yesterday - timedelta(days=offset - 1)
        steps = 8_000 + offset * 100
        rows.append(
            dict(
                day=day,
                step_count=steps,
                water_consumed_ml=2_000,
                water_goal_ml=2_500,
                sleep_total_seconds=27_000,
                sleep_score=80,
                mood_score=6,
                notes=f"day {offset}",
            )
        )
    with db.Session(db.ENGINE) as session:
        for row in rows:
            add_day(session, **row)
        session.commit()
    return rows


def get_charts_blob(html):
    """Extract and parse the `const charts = {...};` JSON injected into the
    page so we can assert on its structure."""
    match = re.search(r"const charts = (\{.*?\});", html, re.DOTALL)
    assert match, "could not find `const charts = {...}` in rendered HTML"
    return json.loads(match.group(1))


def test_dashboard_renders_ok(client):
    seed_representative_week()

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert response.mimetype == "text/html"
    assert response.data


def test_index_redirects_to_dashboard(client):
    response = client.get("/")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")


def test_dashboard_has_all_tabs(client):
    seed_representative_week()

    response = client.get("/dashboard")

    soup = BeautifulSoup(response.data, "html.parser")
    buttons = [b["data-tab"] for b in soup.select(".tab-btn")]
    panels = [p["data-tab"] for p in soup.select(".tab-panel")]
    assert buttons == TAB_NAMES
    assert panels == TAB_NAMES


def test_goals_ladder_progress_matches_done_rungs(client):
    """The Goals tab is driven by goals.json (read fresh per request). The
    rendered progress count must equal the number of `done` rungs actually
    shown on the ladder, and the summit rung must be present."""
    seed_representative_week()

    response = client.get("/dashboard")

    soup = BeautifulSoup(response.data, "html.parser")
    goals_panel = soup.select_one('.tab-panel[data-tab="goals"]')
    done_rungs = goals_panel.select(".rung.done")
    progress_num = goals_panel.select_one(".goals-progress-num")

    assert goals_panel.select_one(".rung.summit") is not None
    assert done_rungs, "expected at least one completed rung"
    # The big number before the "/ total" reflects the done count.
    assert progress_num.get_text().strip().startswith(str(len(done_rungs)))


def test_goals_ladder_prefers_db_row_over_bundled_file(client):
    """When the `goals` table has a row, the Goals tab renders it (not the
    committed goals.json fallback), and the derived progress reflects it."""
    seed_representative_week()
    with db.Session(db.ENGINE) as session:
        session.add(
            db.Goals(
                data={
                    "summit": {"date": "Someday", "title": "DB-sourced summit"},
                    "phases": [
                        {
                            "name": "Only phase",
                            "rungs": [
                                {"date": "d1", "title": "DB rung one", "status": "done"},
                                {"date": "d2", "title": "DB rung two", "status": "current"},
                            ],
                        }
                    ],
                }
            )
        )
        session.commit()

    response = client.get("/dashboard")

    soup = BeautifulSoup(response.data, "html.parser")
    goals_panel = soup.select_one('.tab-panel[data-tab="goals"]')
    assert "DB-sourced summit" in goals_panel.get_text()
    # 3 rungs total (summit + 2), 1 done -> "1 / 3".
    assert goals_panel.select_one(".goals-progress-num").get_text().strip().startswith("1")
    assert len(goals_panel.select(".rung.done")) == 1


def test_steps_tab_is_active_by_default(client):
    seed_representative_week()

    response = client.get("/dashboard")

    soup = BeautifulSoup(response.data, "html.parser")
    active_btn = soup.select_one(".tab-btn.active")
    active_panel = soup.select_one(".tab-panel.active")
    assert active_btn["data-tab"] == "steps"
    assert active_panel["data-tab"] == "steps"


def test_charts_blob_is_valid_and_complete(client):
    seed_representative_week()

    response = client.get("/dashboard")

    charts = get_charts_blob(response.get_data(as_text=True))
    assert EXPECTED_CHART_KEYS <= set(charts)
    assert charts["recent"]["steps"]  # non-empty for a seeded dataset


def test_seeded_step_values_flow_into_charts(client):
    rows = seed_representative_week()

    response = client.get("/dashboard")

    charts = get_charts_blob(response.get_data(as_text=True))
    expected_steps = [row["step_count"] for row in rows]
    assert charts["recent"]["steps"] == expected_steps


def test_milestone_projection_bar_is_in_template(client):
    # The projection bar is computed in client-side JS (no JS execution here),
    # so assert the projection logic ships in the rendered page rather than its
    # computed output. Pins that the feature isn't accidentally dropped.
    seed_representative_week()

    response = client.get("/dashboard")

    html = response.get_data(as_text=True)
    assert "(proj.)" in html
    assert "daysSinceLast" in html


def test_notes_are_not_rendered_unescaped(client):
    with db.Session(db.ENGINE) as session:
        add_day(
            session, date.today() - timedelta(days=1), notes="<script>alert(1)</script>"
        )
        session.commit()

    response = client.get("/dashboard")

    html = response.get_data(as_text=True)
    assert "<script>alert(1)</script>" not in html
