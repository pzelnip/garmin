# Flask/SQLModel → Django migration plan

## Context

The dashboard has grown from a Garmin sync script into a personal health tracker, and the owner now wants to give **controlled, role-based access** to other people:

- **Clinician** (physio/doctor): read-only access to every visualisation panel, but **never notes**.
- **Friend**: can see **only the owner's step graphs**, as a playful competition. Friends' own Garmin data is **out of scope**: `DayStats` stays single-tenant.

Today auth is a single shared password (`DASHBOARD_PASSWORD`, one hard-coded `Owner` user in [src/app.py:52](src/app.py#L52)), so there's no concept of who is viewing. Django gives real users, groups and permissions, plus an admin to manage them. Hosting is unchanged: the app stays on the Pi and remote viewers come in through the existing ngrok tunnel. Public hosting is explicitly deferred.

### What the code looks like now (and what that means for the move)

| Area | Today | Migration implication |
|---|---|---|
| Models | `DayStats`, `Goals`, `StepTarget` in [src/db.py](src/db.py); tables `daystats`, `goals`, `steptarget` on Neon | Map to Django models with `db_table=` and fake the initial migration |
| `Source` enum | SQLAlchemy `Enum(Source)`, which is a **native PG enum type** storing names (`manual_entry`/`garmin`/`fitbit`) | Django has no native PG enum, so convert the column to varchar (see risks) |
| Notes | `notes` column **on `DayStats`** | Django permissions are per model, not per field, so split notes into their own model |
| Dashboard payload | `build_dashboard_data()` builds one big dict, cached in a module-level `_CACHE` ([src/dashboard_data.py:750](src/dashboard_data.py#L750)); **all charts are dumped into the page** via `charts_json \| safe` ([src/dashboard.jinja2:2817](src/dashboard.jinja2#L2817)) | The payload has to be **filtered per role before rendering**, or hiding tabs only hides them visually and the data still leaks |
| Template | 5.7k-line `dashboard.jinja2` (CSS + HTML + JS) | Keep it as is using Django's **Jinja2 template backend**, with no rewrite to DTL |
| Pure logic | `analytics.py`, `dashboard_data.py`, `notes_html.py` operate on lists of entries by attribute name | Mostly reusable unchanged: Django instances duck-type the same attributes |
| Ingest | `src/garmin.py` CLI + `scripts/run-garmin.sh` cron | Becomes `manage.py sync_garmin` |
| Goals tooling | `misc_scripts/{edit,push,validate}_goals.py` import `db` | `push_goals` switches to the Django ORM; the validator is pure and unchanged |
| JS writes | `fetch` PUT/DELETE to `/api/day`, `/api/step-plan` with **no CSRF** | Django CSRF will reject these until the JS sends `X-CSRFToken` |
| Tests | pytest + Flask test client + in-memory SQLite via `conftest.py` monkeypatching `db.ENGINE` | Port to `pytest-django`; the SQLite test DB still works |

---

## Target design

### Layout
```
manage.py
config/            settings.py, urls.py, wsgi.py
health/            the single Django app
  models.py        DayStats, DayNote, Goals, StepTarget (+ permission-holder Meta)
  views.py         dashboard + JSON API (ported from src/app.py)
  access.py        role → visible tabs/payload keys; payload filter
  dashboard_data.py, analytics.py, notes_html.py   (moved from src/, minimal edits)
  management/commands/sync_garmin.py   (ported from src/garmin.py)
  jinja2/health/dashboard.jinja2, login.jinja2
  migrations/
```
Drop `src/` as the import root at cutover. `misc_scripts/` call `django.setup()` (or become management commands).

### Permissions model
Custom permissions declared on `DayStats.Meta.permissions` (one per gated capability), assigned to **Groups** in the admin:

| Permission | Owner | Clinician | Friend |
|---|---|---|---|
| `view_steps` (Steps tab) | ✓ | ✓ | ✓ |
| `view_health` (Water, Sleep, Mood, Day stats, Goals) | ✓ | ✓ | |
| `view_daynote` (built-in perm on `DayNote`: notes panel, search, export) | ✓ | | |
| `edit_journal` (notes/mood PUT, step-plan writes, Step Planning tab) | ✓ | | |
| `run_ops` (debug panel, force-update, sync buttons) | ✓ | | |

The owner is a superuser. `Clinician` and `Friend` are Groups created by a data migration, so they exist in every environment.

**Enforcement lives in one place, `health/access.py`:**
- `visible_tabs(user)` controls which tab buttons and panels the template renders.
- `filter_payload(data, user)` returns a copy of the cached full payload with disallowed sections and `charts` keys **removed**. The cache still holds the one full build; filtering happens per request.
- A global "login required" middleware replaces Flask's `before_request` gate, so new views fail closed. API views use a `@require_perm(...)` decorator that returns 403 JSON.
- `/api/day/<date>` omits `notes` unless the user has `view_daynote`.

---

## Phased tasks

Each phase is a separate reviewable change. Flask stays the live app until Phase 6.

### Phase P: Make the Flask app port-ready (no Django yet)

Ordinary refactors that ship to the Pi one at a time under the current conventions: manual SQL, Flask, and `make test`. Each one shrinks the eventual port to swapping out one well-defined layer. The two risky schema changes happen here, on a stack you know, instead of during the Django cutover.

1. **Parity snapshot tests first.** Snapshot the `data`/`charts` keys and a few headline values from `build_dashboard_data()` against a seeded DB. Snapshot the JSON shape of each `/api/*` response too. Every later refactor, and the Django port itself, must keep these green.
2. **Put all DB access behind one module.** Views in [src/app.py](src/app.py) currently call `session.exec(select(...))` inline, in roughly 15 places. Move every query and write into named functions in a `src/queries.py`, for example:
   - `get_day(day)`
   - `upsert_day_fields(day, **fields)`
   - `set_step_targets(days, target)`
   - `search_notes(q)`
   - `get_goals_data` (moved from `db.py`)
   The views then become ORM-free, and the Django port only has to reimplement `queries.py`.
3. **Make the computation layer pure.** `build_dashboard_data()` and `build_streaks()` currently fetch their own rows (`get_all_entries` is called inside [src/dashboard_data.py:257](src/dashboard_data.py#L257)). Change them to take `entries` (plus `today`) as arguments, so they move to Django untouched, and put the module-level cache behind a tiny `get/set/invalidate` interface.
4. **Split Garmin fetching from persisting.** In [src/garmin.py](src/garmin.py), separate the "Garmin API → plain dict of `DayStats` fields" step from the upsert. The fetch half is framework-agnostic and ports as-is, which leaves only the upsert for the Django port.
5. **Schema change: split notes out** into a `daynote` table (`day` unique, `text`). Write the SQL in `sql/migrations.sql`, a copy-over statement, and SQLModel `DayNote`. Then update `queries.py`, notes search/export and `/api/day`. Keep the old `daystats.notes` column until Phase 7 as a rollback path.
6. **Schema change: `source` enum → varchar.** Switch to `ALTER COLUMN source TYPE varchar(20) USING source::text` with `Enum(Source, native_enum=False)` in SQLModel. Verify that reads and writes work on a Neon branch before applying to prod.
7. **Build `access.py` as a pure function now.** Add `visible_tabs(perms)` and `filter_payload(data, perms)`, where `perms` is a plain set of strings. In Flask the owner always has every permission, so behaviour is unchanged. Write the per-role leak tests now, calling the functions directly. Django later just supplies `perms` from `user.get_all_permissions()`.
8. **Route all JS `fetch` calls through one helper** that already sends an `X-CSRFToken` header read from a `<meta>` tag. Flask ignores the header, and Django will need it.
9. **Make the template stop depending on Flask.** Pass URLs and flags into the Jinja context instead of calling `url_for` and `current_user` inline. The Jinja2 template can then render under either framework.

### Phase 0: Prep for the port
1. Create a **Neon branch** (`django-migration`) off production for all rehearsal. Point the Mac's `.envrc` `CONN_STR` at it while porting.
2. Take a `pg_dump` of production `daystats`/`daynote`/`goals`/`steptarget`.
3. Confirm the Pi's Python 3.14 + Django version pairing (Django 5.2 LTS ≥5.2.8 or 6.0 both support 3.14), then pin it in `pyproject.toml` and add `django`, `gunicorn`, `whitenoise`, `pytest-django`, `django-axes`.

### Phase 1: Django skeleton beside Flask
1. Run `django-admin startproject config .` and `startapp health`.
2. In settings:
   - Read `CONN_STR` via `dj-database-url` (or parse it by hand).
   - Set `DEFAULT_AUTO_FIELD = "django.db.models.AutoField"`. **Not** BigAutoField, which would ALTER the existing `id` columns.
   - Set `TIME_ZONE` to match the Pi's.
   - Read `SECRET_KEY` from `DASHBOARD_SECRET_KEY`.
   - Set `SESSION_COOKIE_AGE` to 30 days, which mirrors `REMEMBER_COOKIE_DURATION`.
   - Set `CSRF_TRUSTED_ORIGINS` to the ngrok domain.
3. Configure **two TEMPLATES backends**: DTL for the admin, and Jinja2 for the dashboard. The Jinja2 environment exposes `url()`, `static()`, `csrf_token` and a `perms` helper.
4. **Two versions, but not two production apps.** During Phases 1–5:
   - The Pi runs only Flask, against prod Neon, exactly as today.
   - Django runs only on the Mac (`manage.py runserver`, port 9330), against the **Neon branch**.

   This is the same dev-vs-prod split you already have. Nothing new is deployed to the Pi until Phase 6, and the Django code can sit on a long-lived `django` git branch so pushes to `main` (which the Pi pulls) stay Flask-only. Port 9330 only matters if you want both apps up on the Mac at once to compare them side by side. The cost is that schema changes during this window must stay compatible with both apps, which is another reason Phase P does them first.

### Phase 2: Models over the existing tables
Phase P already did the notes split and the enum conversion, so this phase only *describes* the existing schema to Django.

1. Write `DayStats`, `DayNote`, `Goals` (`JSONField`) and `StepTarget` with `db_table` set to `daystats`/`daynote`/`goals`/`steptarget`. Match column names, nullability and the `unique` on `day`.
   - Map `source` to `CharField(choices=…)` storing the existing names.
   - Copy over the `@property` helpers and `match_snippet`.
   - Leave the legacy `daystats.notes` column off the model. Django ignores columns it doesn't know about.
2. `makemigrations`, review with `sqlmigrate`, then on the Neon branch run `migrate --fake-initial` and confirm `migrate --plan` and `makemigrations --check` are clean (no diff vs the real schema).
3. Write a data migration that creates the `Clinician`/`Friend` groups with their permissions.
4. Register everything in the admin. `DayNote` is visible to superusers only.

### Phase 3: Read paths, auth and permissions
1. Move `analytics.py`, `dashboard_data.py`, `notes_html.py` and `access.py` into `health/` untouched, since Phase P made them pure. Reimplement the read half of `queries.py` with the Django ORM. Back the cache interface with Django's cache framework (`FileBasedCache` or `DatabaseCache`), so invalidation works across gunicorn workers.
2. Port the views:
   - `/login` and `/logout` use Django auth views rendering `login.jinja2` (username + password).
   - `/dashboard`
   - `/api/day/<date>` GET
   - `/api/step-plan` GET
   - `/api/notes/search` and `/api/notes/export`, now querying `DayNote`
3. Wire `access.py` to `request.user.get_all_permissions()` and add the login-required middleware. Gate the tab buttons, panels and keyboard hotkeys (1–4…) in the template on `visible_tabs`. The localStorage-persisted tab must fall back to the first visible tab.
4. Port `_load_goals` ([src/app.py:161](src/app.py#L161)) unchanged apart from the ORM call.
5. Add `django-axes` (login lockout) and password validators. Clinicians will be logging in from outside.

### Phase 4: Write paths
1. Reimplement the write half of `queries.py` with the ORM (`update_or_create`). Then port the PUT `/api/day/<date>` handler and the `/api/step-plan` PUT/DELETE handlers (single + bulk). These are thin, because Phase P moved the logic out of them.
2. Fill the CSRF `<meta>` tag from Django. The JS helper already sends the token (Phase P).
3. Port `/api/force-update` and `/api/sync` behind `run_ops`. Update the script paths and the systemd unit name if it changes.

### Phase 5: Ingest and tooling
1. `health/management/commands/sync_garmin.py` reuses the framework-agnostic fetch half of [src/garmin.py](src/garmin.py) (split out in Phase P) and reimplements only the upsert, `process_range`, `--auto` and `--resync`. Preserve the rules that the UPSERT keeps manual fields and that `manual_entry` rows don't count as synced.
2. Update `scripts/run-garmin.sh` to call `manage.py sync_garmin --auto`. The healthcheck ping is unchanged.
3. Update `misc_scripts/push_goals.py` and `edit_goals.py` to use the Django ORM via `django.setup()`. `validate_goals.py` is untouched.
4. Update the Makefile targets: `run-server`, `pull-and-post`, `resync`, `remote-dashboard`, `test`, and new `migrate` / `createsuperuser` targets.

### Phase 6: Cutover on the Pi
1. On a quiet evening, after the 2:15 AM cron has run:
   - `pg_dump`
   - run `migrate --fake-initial` against **production** Neon (this only adds Django's own tables, because the schema changes already landed in Phase P)
2. `scripts/run-server.sh` execs `gunicorn config.wsgi -b 0.0.0.0:9329 --workers 2`, with whitenoise for static. Prefer this over `runserver`: on the same systemd unit it costs nothing extra, and it never serves Django's debug pages to someone coming in through the tunnel.
3. Create the owner superuser and the clinician/friend users in the admin.
4. **ngrok**: today's Google OAuth gate is restricted to the owner's single account, which would lock out physio and friends. Either widen the traffic policy to an email allow-list or drop the OAuth layer and rely on Django auth + axes. Update [docs/remote-access.md](docs/remote-access.md).
5. Watch the next 2:15 AM cron run and its healthcheck ping.

### Phase 7: Cleanup
1. Delete `src/app.py`, `src/db.py`, `src/garmin.py`, Flask, flask-login and sqlmodel. Then drop the legacy `daystats.notes` column, as a Django migration.
2. Update `CLAUDE.md`, `AGENTS.md`, `README.md`, `docs/deployment.md` and `docs/testing-dashboard-ui.md`, notably:
   - the "manual SQL migrations" convention becomes "Django migrations"
   - the `src/` import-root guidance
   - the "multi-user/auth out of scope" line, which becomes "multi-viewer, single data owner"

---

## Risks

1. **Data leakage via the page payload** (highest). Gating only the tab buttons leaves notes and health data in `charts_json`, `data` and the API responses. *Mitigation*: filter centrally in `access.py`, plus a test per role asserting that disallowed keys and note text are **absent from the response bytes**.
2. **Cache crossing roles.** If anything per-user (rendered HTML, filtered payload) gets cached under a shared key, a friend can be served the owner's page. *Mitigation*: cache only the unfiltered payload and filter on every request. Never use per-view page caching.
3. **Schema drift on the production DB.** `--fake-initial` against hand-made tables, `SERIAL` vs identity ids and constraint names can all produce a migration that ALTERs or fails on prod. *Mitigation*: Phase P removes the two real schema changes from the cutover. For the rest, rehearse on the Neon branch, review `sqlmigrate`, require `makemigrations --check` to be clean, and take a `pg_dump` first.
4. **Phase P refactors regress the live app.** The refactors ship to the Pi under Flask. *Mitigation*: write the snapshot tests first, ship one refactor per change, and keep the old `daystats.notes` column as a rollback path.
5. **Silent write failures.** CSRF or URL changes could break notes autosave, so the owner loses journal entries without noticing. *Mitigation*: write tests for each PUT/DELETE, and manually confirm in the browser that the autosave indicator reaches "saved" and the value survives a reload.
6. **Ingest regression.** Missing days or overwritten mood/notes after the `sync_garmin` port. *Mitigation*: port the existing `tests/test_garmin.py` first, keep the healthcheck, and keep the SQLModel ingest runnable until Phase 7 as a fallback.
7. **Wider attack surface.** More accounts, some held by non-technical people, all coming in through a tunnel to a home Pi holding health data. *Mitigation*: axes lockout, strong passwords, a `last_login` review in the admin, and removing ngrok's single-account gate deliberately rather than accidentally.
8. **Rewrite fatigue.** A long-lived `django` branch drifts from `main`. *Mitigation*: Phase P puts most of the change on `main` as ordinary refactors, which keeps the branch small. Rebase it often.

## Drawbacks of moving to Django (what you give up or what gets harder)

- **Most of the work the sharing needs isn't covered by Django's built-ins.** Django's permissions are per model (CRUD), with no field-level or row-level permissions. "Everything except notes" and "steps only" still mean custom code in `access.py` plus a schema split. The admin is where *you* manage users and groups; clinicians and friends still use the custom dashboard, not the admin.
- **The same outcome is possible in Flask.** A `users` table, hashed passwords and a role column in Flask would also cover these two use cases. Django's real gains are the admin for managing people, password hashing/reset, CSRF, session security, and ecosystem pieces (axes, django-otp for 2FA) that matter more once it's public.
- **Migrations framework.** You give up the "hand-written SQL you fully control" convention. Django now owns the schema and adds about 10 framework tables (auth, sessions, contenttypes, admin log) to Neon.
- **Jinja2 is second-class in Django.** The admin and most third-party apps assume DTL, and context processors, `csrf_token` and `url` work differently under Jinja2. You'll maintain two template dialects, or face a 5.7k-line rewrite later.
- **More moving parts and ceremony**: `settings.py`, `manage.py`, `DJANGO_SETTINGS_MODULE`, and `django.setup()` in every one-off script (this changes the `misc_scripts` pattern). Startup and memory are higher on the Pi, though fine for Pi 4-class hardware.
- **Weaker static typing**: you lose SQLModel's Pydantic-typed models. The Django ORM needs `django-stubs` for comparable type checking.
- **Tests get heavier**: `pytest-django` DB setup and teardown is slower than a hand-rolled in-memory engine, and the existing fixtures all need rewriting.
- **Rewrite cost**: roughly 700 lines of Flask views, the ingest, and the goals tooling, for zero user-visible features until cutover. Phase P helps here: it delivers real value (tests, a cleaner layout, notes isolation) even if the Django port stalls, and leaves a later "add users to Flask instead" option open.

---

## Verification

- **Phase P**: `make test` stays green after every refactor, including the snapshot and `access.py` leak tests. Each refactor gets the normal browser check on the Pi after push.
- **Parity**: the Phase P snapshot test passes against the Django `build_dashboard_data()` on the same seeded DB.
- **Permission tests** (pytest-django `Client`, one user per group):
  - Friend: the `/dashboard` response contains Steps data only. No `sleep`/`water`/`mood` chart keys and no note text. Health APIs return 403.
  - Clinician: all panels are present. Notes text is absent from `/dashboard` and `/api/day/<d>`. `/api/notes/*` returns 403. Every PUT/DELETE returns 403.
  - Owner: full parity with today. Writes round-trip.
  - Anonymous: `/dashboard` redirects to login, and `/api/*` returns 401/403.
- **Schema**: on the Neon branch, `migrate --plan` shows only the expected migrations, `makemigrations --check` is clean, and row counts and a notes checksum match before and after the notes split.
- **Ingest**: ported `test_garmin.py` passes. On the Pi, `manage.py sync_garmin --resync <recent day>` keeps that day's notes and mood, and the next 2:15 AM cron pings healthchecks.io.
- **Browser** (manual, as per project convention): log in as each of the three roles through the ngrok URL and confirm:
  - the right tabs appear and the hotkeys skip hidden tabs
  - notes autosave survives a reload (as owner)
  - the debug panel is owner-only
