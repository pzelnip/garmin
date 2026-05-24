# Setup

Steps to reproduce the deployment running on the Raspberry Pi (or any new
host). Paths below assume `/home/pi/temp/sandbox/garmin` — adjust as needed.

## 1. System prerequisites

- Python 3.13+ (project uses `requires-python = ">=3.13"` per `pyproject.toml`)
- `git`, `cron`, `curl`, `psql` (PostgreSQL client; used by `db_sess.sh`)
- [`uv`](https://github.com/astral-sh/uv) for dependency management:

```shell
  curl -LsSf https://astral.sh/uv/install.sh | sh
```

- A Postgres database reachable from the Pi (local or hosted). SQLite also
  works via `CONN_STR`; the codebase uses SQLAlchemy/SQLModel and is
  agnostic about backend.

## 2. Clone and install dependencies

```shell
cd /home/pi/temp/sandbox
git clone git@github.com:pzelnip/garmin.git
cd garmin
uv sync
```

`uv sync` creates `.venv/` and installs everything from `uv.lock`. The
wrapper script and Makefile expect `.venv/bin/python`.

## 3. Configure environment variables

Copy the example file and fill in the values:

```shell
cp .envrc.example .envrc
$EDITOR .envrc
```

Required variables:

- `GARMIN_EMAIL` / `GARMIN_PASSWORD` — Garmin Connect credentials.
- `CONN_STR` — SQLAlchemy connection string. Example for Postgres:
  `postgresql://user:password@host:5432/dbname`
- `HEALTHCHECKS_URL` — ping URL from the
  [healthchecks.io](https://healthchecks.io) check that monitors the cron
  job (see step 6).

`.envrc` is gitignored. The wrapper script sources it directly (no `direnv`
required), so plain `export FOO=bar` lines are enough.

## 4. Initialize the database

The schema is created automatically on first run via SQLModel:

```shell
source .envrc
.venv/bin/python -c "import sys; sys.path.insert(0, 'src'); from db import _init_db; _init_db()"
```

Confirm with `./scripts/db_sess.sh` (opens a `psql` shell against `CONN_STR`)
— you should see `daystats` and `stepstoday` tables.

## 5. Smoke-test the script

```shell
.venv/bin/python src/garmin.py --auto
```

This pulls the last 7 days from Garmin and writes rows to `daystats`.
Re-running is idempotent: existing days are read from the DB rather than
re-fetched.

For backfilling a date range:

```shell
.venv/bin/python src/garmin.py --backfill 2024-01-01 2024-01-31
```

## 6. Set up healthchecks.io monitoring

1. Create a free account at <https://healthchecks.io>.
2. Create a new check (e.g. "garmin-daily"):
   - **Period**: 1 day
   - **Grace**: ~1 hour
3. Add a notification integration (email, Slack, ntfy — whatever).
4. Copy the ping URL (`https://hc-ping.com/<uuid>`) into `.envrc` as
   `HEALTHCHECKS_URL`.

## 7. Install the cron job

`scripts/run-garmin.sh` (in the repo, executable) sources `.envrc`, runs
`src/garmin.py --auto`, then pings `HEALTHCHECKS_URL` on success. If the
script fails, the ping doesn't fire and healthchecks.io alerts after the
grace period.

Open the user's crontab:

```shell
crontab -e
```

Add:

```shell
0 5 * * * /home/pi/temp/sandbox/garmin/scripts/run-garmin.sh >> /home/pi/temp/sandbox/garmin/cron.log 2>&1
```

This runs daily at 05:00 local time. Verify:

```shell
crontab -l
sudo systemctl status cron    # confirm the cron daemon is running
```

Test the wrapper interactively before waiting for cron:

```shell
/home/pi/temp/sandbox/garmin/scripts/run-garmin.sh
```

Check that healthchecks.io shows a green ping immediately after.

## 8. (Optional) Run the Flask dashboard

The dashboard at `/dashboard` reads from the same DB and serves
visualizations over LAN:

```shell
.venv/bin/python src/app.py
```

The server listens on `0.0.0.0:9329`. Browse to
`http://<pi-ip>:9329/dashboard` from any LAN device.

To keep it running, add a `systemd` unit or run under `tmux`/`screen`.
There's no production WSGI server configured — it's Werkzeug dev server,
which is fine for personal LAN use but should not be exposed to the
internet.

## Troubleshooting

- **`crontab` job runs but nothing happens**: cron has a minimal env. The
  wrapper handles this by sourcing `.envrc`. Make sure `.envrc` exists and
  is sourceable as plain bash (no `direnv`-only syntax like `dotenv` or
  `PATH_add`).
- **Garmin login fails**: Garmin sometimes requires MFA. The `garminconnect`
  library handles this via cached tokens — first login may need to be done
  interactively from the Pi.
- **Healthchecks not pinging despite successful run**: confirm
  `HEALTHCHECKS_URL` is exported (echo it after sourcing `.envrc`), and that
  the Pi has outbound HTTPS to `hc-ping.com`.
