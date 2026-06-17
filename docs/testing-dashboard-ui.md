# Dashboard UI testing

The dashboard is tested in two layers. Layer 1 ships today; layer 2 is
documented here as an optional next step.

## Layer 1 — server-rendered HTML (implemented)

`tests/test_dashboard_render.py` renders `/dashboard` against an in-memory
SQLite DB seeded with a representative two weeks of data, then asserts the
**data → template → page contract**:

- the page renders (200, `text/html`) and `/` redirects to it;
- all five tabs (Steps / Water / Sleep / Mood / Day) are present and Steps is
  active by default;
- the injected `const charts = {…}` blob is valid JSON and exposes every
  expected top-level key (renaming a key in `dashboard_data.py` fails the
  `test_charts_blob_is_valid_and_complete` test);
- seeded step values flow through into the chart arrays;
- notes are not emitted unescaped (autoescape config holds);
- an empty DB still renders without throwing.

The Day-tab mutation endpoints (`GET /api/day/<iso>`, `PUT …/notes`,
`PUT …/mood`, `POST /api/force-update`) are covered at the API level in
`tests/test_app.py`.

Shared test infrastructure:

- `tests/conftest.py` — the `client` fixture (in-memory SQLite + Flask test
  client) and the `force_sqlite` guard.
- `tests/helpers.py` — `add_day` / `seed` / `seed_rows` data seeders.
- `test_dashboard_render.py` owns a `fresh_dashboard_cache` autouse fixture
  that calls `invalidate_dashboard_cache()` around each test, because
  `get_dashboard_data_cached()` has a 5-minute process-local cache that would
  otherwise serve one test's data to the next.

Run with `make test`. These add no runtime deps (only `beautifulsoup4` in the
dev group for readable HTML assertions).

## Layer 2 — real browser (Playwright, optional, not implemented)

Layer 1 cannot exercise the inline JS (tab switching, hotkeys, localStorage,
the Day-tab fetch flow, notes search, the debug panel). If that logic grows
enough to warrant coverage, add a headless-browser suite:

- add `pytest-playwright` to the `dev` group; run `playwright install chromium`;
- add a live-server fixture: start `app` on a real port in a background thread
  (pointed at a seeded SQLite engine), yield the base URL, tear down;
- mark these tests `@pytest.mark.browser` and register the marker, so the fast
  suite stays the default via `uv run pytest -m "not browser"`.

Highest-value behaviours to cover:

- tab switching + hotkeys `1`–`5` + `localStorage["dashboard.activeTab"]`
  persistence (and hotkeys ignored while typing in an input/textarea);
- lazy chart init: first switch to a tab builds its Chart.js instances with no
  console errors;
- Day-tab fetch flow: picker / prev-next / today-yesterday triggers
  `GET /api/day/<iso>` and populates the cards; Save fires the two PUTs;
- notes search: ≥3 chars (debounced) hits `/api/notes/search` and renders
  results; clicking a result jumps to that day;
- debug panel: `?` opens, `Escape` closes, force-update button POSTs.

Kept optional because browser tests are heavier and flakier and the template's
JS rarely changes; the layer-1 contract catches the regressions most likely to
bite.
