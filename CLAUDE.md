# Claude.md

A personal Garmin / health-tracking dashboard. One user (the owner of
this repo). Runs in two places: a Raspberry Pi (production) and the
owner's Mac (local dev).

## What this project does

- **`src/garmin.py`** — daily ingest job. Pulls per-day stats from the
  Garmin Connect API (steps, sleep, hydration, biometrics) and writes
  one `DayStats` row per day to Postgres.
- **`src/app.py`** — Flask dashboard at `/dashboard`. Reads from the
  same DB and renders a single-page UI with multiple tabs (Steps,
  Water, Sleep, Day, plus per-day notes / mood / lifestyle-logging).
- **`src/dashboard.jinja2`** — the single big template that drives the
  dashboard. CSS, HTML, and inline JS all live in this one file.
- **Goals tab ladder** — the training "ladder" (milestones: date / title
  / status `done|current|future`, plus the summit). The **live copy lives
  in the Neon `goals` table** (`Goals` model, a single-row JSON blob), read
  *fresh on every request* by `_load_goals` in `app.py`, which derives the
  progress totals. To change it: edit the repo-root **`goals.json`**
  (the authoring source + offline fallback) locally, then publish with
  `./scripts/push-goals.sh` (wraps `misc_scripts/push_goals.py`, which
  upserts the row). This deliberately writes to production Neon and needs
  no deploy/commit/restart — so nothing is committed and there's no
  git-pull merge-conflict risk on the Pi.

## Architecture cheat-sheet

- **DB**: Postgres on Neon (production), accessed via SQLModel /
  SQLAlchemy. No ORM migrations framework — schema changes go in
  [sql/migrations.sql](sql/migrations.sql) by hand and are applied
  manually by the user
- **Data model**: `DayStats` is flat, one row per calendar day, lots
  of `Optional[...]` columns for the various features Garmin may or
  may not have populated. Manual fields (`notes`, `mood_score`) live
  on the same row.
- **Sync model**: `get_from_garmin` UPSERTs by `day` so a row that
  was created from manual entry first (notes/mood for today) keeps
  those fields when Garmin data lands later. `process_range` skips
  days that already have non-manual data; `source == Source.manual_entry`
  rows are *not* considered "synced".
- **Charts**: Chart.js loaded via CDN inside the template. No
  build step. Heatmaps are plain CSS grids of `<div>` cells.
- **Tabs**: client-side toggle, persisted in localStorage. Hotkeys
  `1` / `2` / `3` / `4` switch tabs.

## Project layout

```
src/              # application code (Flask app, ingestion, models, analytics)
sql/migrations.sql  # hand-written ALTER TABLE / CREATE TABLE statements
scripts/          # systemd / cron wrapper scripts (Pi-only)
misc_scripts/     # one-off backfill scripts; deletable once their job is done
docs/             # deployment notes
```

`src/` is the import root: scripts in `misc_scripts/` are run as
`cd src && PYTHONPATH=. ../.venv/bin/python ../misc_scripts/foo.py`
because they `from db import …` directly (no package layout).

## Running it locally

Reads from the production Neon DB (read-only is safe; avoid running
`garmin.py --auto` locally because it would duplicate the Pi's writes).

```bash
source ./.envrc                            # loads CONN_STR + GARMIN_EMAIL/PASSWORD
GARMIN_DASHBOARD_DEBUG=1 ./.venv/bin/python src/app.py
                                           # http://localhost:9329/dashboard
```

`GARMIN_DASHBOARD_DEBUG=1` enables Flask's reloader (template / Python
edits land on refresh). It defaults to `False` so the Pi runs in
production mode without needing the var.

`.envrc` is direnv-managed; if you're in a shell where direnv isn't
active, source it manually with `set -a; source ./.envrc; set +a`
before running anything that needs the Garmin creds.

## Running it on the Pi

Deployed at `/home/pi/temp/sandbox/garmin/`. The app runs under
systemd; daily sync runs from cron. See
[docs/deployment.md](docs/deployment.md) for the full picture. Key
bits:

- `scripts/run-server.sh` — systemd `ExecStart` wrapper (sources
  `.envrc`, exec's `python src/app.py`).
- `scripts/run-garmin.sh` — cron wrapper (5 AM daily). Pulls latest
  git, restarts the service, runs `garmin.py --auto`, then pings
  healthchecks.io.
- `scripts/force-update.sh` — invoked by the dashboard's debug-panel
  "Force update" button.

The Pi pulls from `origin/main` as part of the daily cron, so pushing
to `main` ships changes. There's also a debug-panel button on the
dashboard (toggle with `?` key) that triggers an immediate pull+
restart on the Pi.

## Conventions worth knowing

- **No in-browser JS tests.** This is a personal project. Verification of
  client-side behaviour is "does it render correctly in the browser" and "does
  the cron job's healthcheck still ping". There *is* a pytest layer that
  renders `/dashboard` and asserts the data → template contract
  (`tests/test_dashboard_render.py`); see
  [docs/testing-dashboard-ui.md](docs/testing-dashboard-ui.md). A real-browser
  (Playwright) layer is documented there but intentionally left unimplemented.
  The pytest suite is otherwise minimal.
- **Comments are sparse.** Only comment the *why* when it's
  non-obvious — never the what.
- **Don't run `garmin.py` locally** — it writes to the production DB.
  Use the existing data via the dashboard or psql.
- **Don't push or restart services without being asked.** Local
  edits → user reviews → user pushes when ready.
- **Migrations are manual.** Append to `sql/migrations.sql` with a
  `-- YYYY-MM:` comment header matching the existing entries. Don't
  introduce Alembic or any migration framework unless asked
- **Pylint is not used on this project** Don't add any pylint exception messages

## Probe scripts and discovery work

When exploring a new Garmin Connect endpoint, the established pattern
is a one-off script in `misc_scripts/` (e.g. `probe_lifestyle.py`)
that uses the existing `garmin_api()` context manager from
`src/garmin.py`. Output to stdout, no DB writes. Delete the script
once its discovery work is done — though existing ones (like
`probe_lifestyle.py` and `backfill_sleep.py`) are kept around as
templates per the `misc_scripts/README.md` convention.

## Things that are NOT in scope here

- Multi-user support, auth, accounts — single-user, single-Pi.
- Writing back to Garmin — the library is read-only and the user
  doesn't want that anyway.
- ORM migration frameworks — manual SQL is fine at this scale.
- Build pipelines / bundling — CDN-loaded Chart.js, inline JS.
