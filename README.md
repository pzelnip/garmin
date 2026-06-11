# Garmin Health Dashboard

A personal health-tracking dashboard built around Garmin Connect data.
Runs on a Raspberry Pi (production) and works locally on a Mac for development.

## What it does

- **Daily ingest** (`src/garmin.py`) — pulls per-day stats from the Garmin
  Connect API and writes one row per calendar day to a Postgres database.
  Captured metrics include: steps, distance, floors climbed, heart rate,
  stress, sleep (total / deep / light / REM / awake, plus sleep score),
  hydration, and body composition (weight, BMI, body fat, muscle mass, etc.).
- **Flask dashboard** (`src/app.py`) — served at `/dashboard`. Reads from the
  same DB and renders a single-page UI with multiple tabs:
  - **Steps** — streaks, totals, goal-met days, heatmaps, charts
  - **Water** — hydration trends and goal tracking
  - **Sleep** — sleep duration and score charts
  - **Day** — per-day detail view with editable notes and mood score

## Tech stack

- Python 3.14+, [Flask](https://flask.palletsprojects.com/),
  [SQLModel](https://sqlmodel.tiangolo.com/) / SQLAlchemy
- Postgres (Neon in production); any SQLAlchemy-compatible DB works
- [garminconnect](https://pypi.org/project/garminconnect/) for Garmin API access
- [Chart.js](https://www.chartjs.org/) (CDN) for charts; no build step
- [uv](https://github.com/astral-sh/uv) for dependency management

## Project layout

```shell
src/               # Flask app, ingestion script, models, analytics
sql/migrations.sql # hand-written schema migrations (applied manually)
scripts/           # systemd / cron wrapper scripts (Pi-only)
misc_scripts/      # one-off backfill / probe scripts
docs/              # deployment and setup notes
```

## Running locally

See [docs/SETUP.md](docs/SETUP.md) for full setup instructions (dependencies,
environment variables, DB initialisation, cron job, and systemd service).
