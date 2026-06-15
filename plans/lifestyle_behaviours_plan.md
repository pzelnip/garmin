# Add Garmin Lifestyle Logging support

## Context

Garmin Connect's "Lifestyle Logging" feature lets the user track daily
behaviours (Morning Caffeine, Light Exercise, Mindfulness, Physical
Therapy, …). The mobile app shows ~10 behaviours per day with YES/NO
indicators and, for some, an `amount` (e.g. "1 coffee", "2 sodas").

The user wants two things from this data on the dashboard:

1. **Per-day view** — for a selected day, see which behaviours were
   logged (and the amount for any quantitative ones). Slots naturally
   into the existing Day-view tab.
2. **Per-behaviour analytics** — for a chosen behaviour, see the
   percentage of days it was achieved over a time window.

The aggregate "X of N behaviours logged today" counter that Garmin's
mobile app surfaces is **explicitly not wanted** — the user only cares
about per-behaviour, not the rollup. So no aggregate columns on
`DayStats`.

The API was probed (see `misc_scripts/probe_lifestyle.py`); response
shape, quirks, and library escape hatches are well understood.

## Approach

Three new tables, **normalized**:

- `Behaviour` — one row per behaviour template (~10 total). Holds the
  fields that are functionally dependent on `behaviour_id`
  (`name`, `category`, `measurement_type`, `sleep_related`). These
  change rarely, but when they do (e.g. a rename in the Garmin app)
  the change should land in exactly one place.
- `LifestyleLog` — one row per `(day, behaviour_id)`. Holds only the
  per-day "was this behaviour engaged with" fact: `log_status`.
- `LifestyleLogDetail` — for QUANTITY behaviours, one row per logged
  sub-type. A single `LifestyleLog` can have 0 (NONE behaviour or
  unlogged), 1, or N detail rows. Captures the case where e.g. Late
  Caffeine on one day records both Coffee × 1 *and* Soda × 2.

No changes to `DayStats`. Ingestion is wired into the existing
`get_from_garmin` flow alongside hydration and sleep. The Day-view tab
gets a new "Behaviours" panel; a new "Behaviours" tab (`5`) gets a
per-behaviour analytics view.

### Why normalized (the FK lookup table)

Four shapes were considered before landing here. Brief notes on each:

1. **Denormalized — `name`/`category` stored on every `LifestyleLog`
   row.** Rejected. `name`, `category`, `measurement_type`, and
   `sleep_related` are functional dependencies of `behaviour_id`, not
   of `day` — storing them per-row creates classic FD-violation
   problems:
   - **Update anomaly** — a Garmin-app rename needs an UPDATE across
     every matching row. `session.merge()` only touches the day being
     re-fetched, so older days would keep the stale name forever
     unless we also force a full historical re-ingest.
   - **Inconsistent state risk** — same `behaviour_id` ending up with
     two different `name`s makes `GROUP BY behaviour_id` trustworthy
     but `GROUP BY name` lie.
   - **No source of truth** — schema can't say which of two
     conflicting values is "right".

2. **Dict in code — `{behaviour_id: (name, category, …)}`.** Tempting
   for ~10 mostly-stock behaviours. Real pros: simplest schema (one
   table), no join on the read path, labels are type-checked and
   tweakable in code. Rejected because:
   - Custom behaviours (`Mindfulness`, `Prep`) are user-renameable in
     the Garmin app — a rename there leaves the dict stale until
     someone notices and pushes a code change.
   - Adding/removing behaviours in the app silently produces unknown
     IDs (rendered as `#347806` until the dict is updated).
   - You can mitigate with an ingestion-time "warn on drift" check,
     but that's still a manual deploy loop on every behaviour change
     and doesn't *persist* the corrected mapping.

3. **Soft reference — lookup table with no FK, read into in-memory
   cache.** Tries to keep the dict's "no join" feel while persisting
   drift. Rejected because:
   - Cache coherency between the ingestion job and the Flask app
     becomes a real problem (separate processes, independent caches,
     stale until restart).
   - No referential integrity — orphan `LifestyleLog` rows are
     possible by accident.
   - You end up with the FK approach's table plus the dict approach's
     coordination cost, without the safety of either.
   - The only "saving" is avoiding a JOIN that's free anyway against
     a 10-row lookup with an index.

4. **FK lookup table — `Behaviour` parent + `LifestyleLog` child with
   real FK.** ✅ Chosen. Schema cost is one extra table + one FK
   constraint. Renames in the Garmin app land as one UPDATE on one
   row and every historical query reflects them via the join. New
   behaviours auto-register on next sync (just another upsert).
   Referential integrity is enforced by the DB rather than relied
   upon in code. The join against a 10-row indexed lookup is
   essentially free at any scale we'll see.

Splitting `Behaviour` out as its own table makes a rename one UPDATE
on one row that every join automatically reflects.

### 1. Schema (`src/db.py`)

Three new SQLModels below the existing `DayStats` class:

```python
class Behaviour(SQLModel, table=True):
    """Lookup row for a lifestyle-logging behaviour. id = Garmin's
    behaviourId; small ints (2, 3, …) are stock behaviours shared
    across all Garmin users, large ints (347806, …) are user-created.
    name / category etc. are upserted on every ingest, so a rename in
    the Garmin app propagates to all historical LifestyleLog rows
    automatically via the FK join.
    """

    id: int = Field(primary_key=True)         # = Garmin's behaviourId
    name: str
    category: str                             # LIFESTYLE / SLEEP_RELATED / TREATMENTS / CUSTOM / ...
    measurement_type: str                     # "NONE" or "QUANTITY"
    sleep_related: bool = False


class LifestyleLog(SQLModel, table=True):
    """Per-day record: did behaviour X get logged on day Y? For QUANTITY
    behaviours the actual logged values live on LifestyleLogDetail rows.
    """

    __table_args__ = (UniqueConstraint("day", "behaviour_id"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    day: date = Field(index=True)
    behaviour_id: int = Field(foreign_key="behaviour.id", index=True)
    log_status: Optional[str]                 # "YES" / "NO" / None (untouched on old days)


class LifestyleLogDetail(SQLModel, table=True):
    """For QUANTITY behaviours, one row per logged sub-type per day. A
    single LifestyleLog can have 0..N details — e.g. Late Caffeine
    logged as Coffee × 1 and Soda × 2 on the same day is two rows.
    NONE behaviours and unlogged QUANTITY behaviours have no details.
    """

    __table_args__ = (UniqueConstraint("lifestyle_log_id", "sub_type_id"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    lifestyle_log_id: int = Field(foreign_key="lifestylelog.id", index=True)
    sub_type_id: int                          # Garmin's subTypeId (stable: 1=COFFEE, 2=TEA, 3=OTHER, 4=SODA, 5=ENERGY_DRINK)
    sub_type_name: str                        # snapshot — small fixed enum, low drift risk
    amount: int
```

The `(day, behaviour_id)` unique constraint on `LifestyleLog` is the
natural upsert key for the parent. The `(lifestyle_log_id, sub_type_id)`
constraint on details ensures we can have one Coffee + one Soda per
day, but not two Coffee rows.

`sub_type_name` is snapshotted (not FK'd to a `SubType` lookup table)
because the set is small (~5 values), stable across users, and
effectively never changes — none of the FD-violation pressures that
made the `Behaviour` split necessary apply here.

### 2. Migration (`sql/migrations.sql`)

Append:

```sql
-- 2026-06: lifestyle logging — behaviour lookup + per-day log + per-subtype details.
CREATE TABLE behaviour (
    id               INTEGER PRIMARY KEY,           -- = Garmin's behaviourId
    name             TEXT NOT NULL,
    category         TEXT NOT NULL,
    measurement_type TEXT NOT NULL,
    sleep_related    BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE lifestylelog (
    id               SERIAL PRIMARY KEY,
    day              DATE NOT NULL,
    behaviour_id     INTEGER NOT NULL REFERENCES behaviour(id),
    log_status       TEXT,
    UNIQUE (day, behaviour_id)
);
CREATE INDEX lifestylelog_day_idx ON lifestylelog (day);
CREATE INDEX lifestylelog_behaviour_idx ON lifestylelog (behaviour_id);

CREATE TABLE lifestylelogdetail (
    id               SERIAL PRIMARY KEY,
    lifestyle_log_id INTEGER NOT NULL REFERENCES lifestylelog(id) ON DELETE CASCADE,
    sub_type_id      INTEGER NOT NULL,             -- Garmin's subTypeId
    sub_type_name    TEXT NOT NULL,                -- COFFEE / TEA / OTHER / SODA / ENERGY_DRINK
    amount           INTEGER NOT NULL,
    UNIQUE (lifestyle_log_id, sub_type_id)
);
CREATE INDEX lifestylelogdetail_log_idx ON lifestylelogdetail (lifestyle_log_id);
```

`ON DELETE CASCADE` on the detail FK means if a `LifestyleLog` row
ever gets deleted, its details go too — no orphan rows possible.

### 3. Ingestion hook (`src/garmin.py`)

In `get_from_garmin` ([src/garmin.py:29-89](src/garmin.py#L29)), add a
new try/except block alongside the existing hydration and sleep ones:

```python
try:
    lifestyle = API.get_lifestyle_logging_data(day) or {}
except Exception as ex:
    logging.warning(f"Could not fetch lifestyle for {day}: {ex}")
    lifestyle = {}

reports = lifestyle.get("dailyLogsReport") or []
```

After the `DayStats` insert + commit, upsert behaviours first, then
per-day log rows, then per-subtype detail rows. Order matters because
of the FK constraints:

```python
for entry in reports:
    # 1. Upsert the Behaviour lookup row — picks up renames automatically.
    session.merge(Behaviour(
        id=entry["behaviourId"],
        name=entry["name"],
        category=entry["category"],
        measurement_type=entry["measurementType"],
        sleep_related=entry.get("sleepRelated", False),
    ))

    # 2. Upsert the per-day log row.
    has_log = "logStatus" in entry  # missing => untouched, ignore details[]
    log_row = LifestyleLog(
        day=orig_day,
        behaviour_id=entry["behaviourId"],
        log_status=entry.get("logStatus"),
    )
    log_row = session.merge(log_row)
    session.flush()  # populate log_row.id before we reference it on details

    # 3. Replace this day's detail rows for QUANTITY behaviours.
    #    Delete-then-insert (not upsert) so removing a sub-type in the
    #    Garmin app — e.g. deleting the Coffee entry from Late Caffeine
    #    on a re-fetched day — propagates cleanly.
    if has_log and entry["measurementType"] == "QUANTITY":
        session.exec(
            delete(LifestyleLogDetail)
            .where(LifestyleLogDetail.lifestyle_log_id == log_row.id)
        )
        for d in entry.get("details") or []:
            if d.get("amount") is None:
                continue  # catalogue noise on un-touched days, skip
            session.add(LifestyleLogDetail(
                lifestyle_log_id=log_row.id,
                sub_type_id=d["subTypeId"],
                sub_type_name=d["subTypeName"],
                amount=d["amount"],
            ))
session.commit()
```

A few quirks the code above handles:

- **`has_log` guard** — when `logStatus` is missing on an entry (the
  probe-observed "untouched on old days" case), QUANTITY entries
  return the full configurable subtype *catalogue* in `details[]`
  with no `amount`. Skipping detail-row creation entirely in that
  case avoids polluting the detail table with non-events.
- **Belt-and-suspenders `amount is None` check** — even on logged
  days, a defensive skip if Garmin ever returns a malformed entry.
- **Delete-then-insert for details** (rather than upsert by
  `(log_id, sub_type_id)`) — if you delete a sub-type from a day's
  log in the Garmin app and we re-fetch, the corresponding detail
  row needs to vanish too. Upsert would leave it behind.
- **`session.merge()` on `LifestyleLog`** returns the merged
  instance, which is what we need for `log_row.id` on the details
  insert. The `session.flush()` ensures the ID is populated even on
  the first-ever insert.

### 4. Backfill script (`misc_scripts/backfill_lifestyle.py`)

One-off mirror of [misc_scripts/backfill_sleep.py](misc_scripts/backfill_sleep.py):

- Find all existing `DayStats` days that don't yet have any
  `LifestyleLog` rows (`SELECT DayStats.day FROM daystats WHERE NOT
  EXISTS (...)`).
- Iterate most-recent-first, throttle with `sleep(random())`, call
  `API.get_lifestyle_logging_data(day)`, upsert both `Behaviour` rows
  and `LifestyleLog` rows via the same `session.merge()` logic.
- `--dry-run` flag, per-day try/except, final tally
  (`updated / no_data / failed / total_scanned`).
- Same `garmin_api()` + `db_session()` reuse as the sleep backfill.

### 5. Read endpoint (`src/app.py`)

Extend `day_detail` ([src/app.py:542](src/app.py#L542)) to include
the per-behaviour list. Join `Behaviour` for display fields, then
fetch all of the day's details in a second query and group them by
`lifestyle_log_id` for the response:

```python
rows = session.exec(
    select(LifestyleLog, Behaviour)
    .join(Behaviour, LifestyleLog.behaviour_id == Behaviour.id)
    .where(LifestyleLog.day == target)
    .order_by(Behaviour.category, Behaviour.name)
).all()

# One follow-up query for all details on this day, grouped by log row.
log_ids = [ll.id for ll, _ in rows]
details_by_log = defaultdict(list)
if log_ids:
    for d in session.exec(
        select(LifestyleLogDetail)
        .where(LifestyleLogDetail.lifestyle_log_id.in_(log_ids))
    ).all():
        details_by_log[d.lifestyle_log_id].append(
            {"sub_type_name": d.sub_type_name, "amount": d.amount}
        )

# then add to the returned dict:
"behaviours": [
    {
        "name": b.name,
        "category": b.category,
        "measurement_type": b.measurement_type,
        "log_status": ll.log_status,
        "details": details_by_log.get(ll.id, []),
    }
    for ll, b in rows
],
```

`details` is always a list — empty for NONE behaviours, empty for
unlogged QUANTITY behaviours, otherwise one entry per sub-type
logged that day (e.g. `[{"sub_type_name": "COFFEE", "amount": 1},
{"sub_type_name": "SODA", "amount": 2}]`).

### 6. New analytics endpoints (`src/app.py`)

```python
@app.route("/api/behaviours")
def behaviour_list():
    """All behaviours with their lifetime YES-rate."""
    with db_session() as session:
        rows = session.exec(text("""
            SELECT b.id              AS behaviour_id,
                   b.name            AS name,
                   b.category        AS category,
                   b.measurement_type AS measurement_type,
                   SUM(CASE WHEN ll.log_status = 'YES' THEN 1 ELSE 0 END) AS yes_days,
                   SUM(CASE WHEN ll.log_status IN ('YES','NO') THEN 1 ELSE 0 END) AS tracked_days
            FROM behaviour b
            LEFT JOIN lifestylelog ll ON ll.behaviour_id = b.id
            GROUP BY b.id, b.name, b.category, b.measurement_type
            ORDER BY b.category, b.name
        """)).all()
        return jsonify({"behaviours": [
            {
                "behaviour_id": r.behaviour_id, "name": r.name,
                "category": r.category, "measurement_type": r.measurement_type,
                "yes_days": r.yes_days, "tracked_days": r.tracked_days,
                "yes_pct": round(100 * r.yes_days / r.tracked_days, 1)
                           if r.tracked_days else None,
            } for r in rows
        ]})


@app.route("/api/behaviours/<int:behaviour_id>")
def behaviour_detail(behaviour_id):
    """Per-day history for a specific behaviour — fuels per-behaviour charts."""
    with db_session() as session:
        rows = session.exec(
            select(LifestyleLog)
            .where(LifestyleLog.behaviour_id == behaviour_id)
            .order_by(LifestyleLog.day)
        ).all()
        return jsonify({"behaviour_id": behaviour_id, "days": [
            {"day": r.day.isoformat(), "log_status": r.log_status,
             "amount": r.amount, "sub_type_name": r.sub_type_name}
            for r in rows
        ]})
```

`LEFT JOIN` from `behaviour` ensures behaviours that exist but were
never tracked still show up in the list (with `tracked_days = 0`,
`yes_pct = null`).

"Tracked days" treats missing-`log_status` as untracked (excluded from
the denominator), matching the probe-observed semantics. The yes-rate
denominator is "days you actually engaged with this behaviour", not
"days that existed in the DB" — answers your "what percentage of days
I achieved behaviour X" question cleanly.

### 7. Day-view UI (`src/dashboard.jinja2`)

Add a new "Behaviours" panel to the Day tab, above the stat grid
(below the notes panel from the previous plan). Renders the
`behaviours[]` array from `/api/day/<date>`. Each row is:

- Behaviour name on the left.
- A coloured pill on the right: green check for `YES`, gray dash for
  `NO`, dim "—" for missing `log_status`.
- For QUANTITY behaviours that were logged, render one chip per detail
  row below the YES/NO indicator (e.g. "Coffee 1" and "Soda 2"
  side-by-side) — matches the mobile app's layout for behaviours like
  Late Caffeine that can have multiple sub-types logged simultaneously.
- Group rows by `category` with a subtle uppercase header per group.

CSS mirrors the existing `.day-notes-panel` aesthetic for consistency.

JS extends `renderDay(data)` to populate the new panel from
`data.behaviours`; hidden when the array is empty (e.g. days before
ingestion started, or days where Garmin returned nothing).

### 8. Behaviours tab (`src/dashboard.jinja2`)

A new 5th tab (📋 Behaviours, hotkey `5`) that:

- Calls `/api/behaviours` on first activation, renders one card per
  behaviour grouped by category. Each card shows: name, lifetime
  yes-rate as a big number, "X of Y days" sub-line. Clicking a card
  loads `/api/behaviours/<id>` and renders a per-behaviour chart
  (Chart.js line chart, x = date, y = 0/1 with a 7-day rolling
  yes-rate overlay).
- Follows the same pattern as the existing tab structure:
  `data-tab="behaviours"`, lazy-load on first activation, JS hooks
  into the existing `TAB_HOTKEYS` map + tab-restore.

This is a meaningful UI chunk and worth implementing in a follow-up
step once #1–#7 are working — listed here for completeness but the
implementation order should be: schema/migration/ingestion/backfill
first, then Day-view rendering, then this tab.

## Critical files

- [src/db.py](src/db.py) — add `Behaviour`, `LifestyleLog`, and
  `LifestyleLogDetail` models.
- [sql/migrations.sql](sql/migrations.sql) — append three CREATE
  TABLEs + indexes.
- [src/garmin.py](src/garmin.py) — wire ingestion into
  `get_from_garmin` (upsert Behaviour, then LifestyleLog, then
  delete-and-reinsert LifestyleLogDetail rows for QUANTITY entries).
- [misc_scripts/backfill_lifestyle.py](misc_scripts/backfill_lifestyle.py) —
  one-off backfill for existing DayStats days.
- [src/app.py](src/app.py) — extend `day_detail` (join to Behaviour
  + second query for details grouped by log_id), add `/api/behaviours`
  + `/api/behaviours/<id>` routes.
- [src/dashboard.jinja2](src/dashboard.jinja2) — Day-view behaviours
  panel (rendering one chip per detail row) + new Behaviours tab.

## Reused existing pieces

- `garmin_api()` + `db_session()` — same context-manager pattern as
  the sleep backfill at
  [misc_scripts/backfill_sleep.py](misc_scripts/backfill_sleep.py).
- Hydration / sleep try/except idiom in `get_from_garmin`
  ([src/garmin.py:39-49](src/garmin.py#L39)) — copy-paste shape for
  the new lifestyle try/except.
- Tab + hotkey wiring in `dashboard.jinja2` —
  `TAB_HOTKEYS = { '1': 'steps', '2': 'water', '3': 'sleep',
  '4': 'day' }` becomes `{ ..., '5': 'behaviours' }`.
- `renderDay(data)` + `loadDay(iso)` pattern — extend, don't
  duplicate.

## Verification

1. **Apply migration on Neon** via `./scripts/db_sess.sh`:
   ```sql
   \i sql/migrations.sql
   ```
   Confirm all three tables exist: `\d behaviour`, `\d lifestylelog`,
   `\d lifestylelogdetail`.

2. **Trigger a single-day ingest** locally:
   ```bash
   cd src && PYTHONPATH=. ../.venv/bin/python -c \
     "from datetime import date; from garmin import garmin_api, get_from_garmin; \
      from db import db_session; \
      with garmin_api(), db_session() as s: get_from_garmin(date(2026,6,6), s)"
   ```
   Then in psql, verify the parent rows:
   ```sql
   SELECT b.name, b.category, ll.log_status
   FROM lifestylelog ll JOIN behaviour b ON b.id = ll.behaviour_id
   WHERE ll.day = '2026-06-06' ORDER BY b.category, b.name;
   ```
   And the details:
   ```sql
   SELECT b.name, lld.sub_type_name, lld.amount
   FROM lifestylelogdetail lld
   JOIN lifestylelog ll ON ll.id = lld.lifestyle_log_id
   JOIN behaviour b ON b.id = ll.behaviour_id
   WHERE ll.day = '2026-06-06'
   ORDER BY b.name, lld.sub_type_name;
   ```

3. **Verify multi-detail capture** — pick a day where you know you
   logged multiple sub-types for one behaviour (e.g. Late Caffeine
   with Coffee AND Soda). Confirm the detail query shows two rows
   for that behaviour on that day, not one.

4. **Verify upsert behaviour** — re-run the same ingest; row counts
   stay flat on all three tables, `behaviour.id` and `lifestylelog.id`
   unchanged. (`lifestylelogdetail.id` values may change since we
   delete-and-reinsert, which is expected.)

5. **Verify detail-delete propagation** — manually remove a sub-type
   from a day's log in the Garmin app (e.g. delete the Coffee entry
   from a Late Caffeine that had both Coffee and Soda), wait for
   sync, re-ingest the day, confirm the corresponding detail row is
   gone from `lifestylelogdetail`.

6. **Verify rename propagation** — manually rename a behaviour in
   psql, re-ingest the day, confirm the `behaviour` row's `name`
   updates and the per-day query immediately shows the new name
   (because there's only one source).
   ```sql
   UPDATE behaviour SET name = 'TEST-RENAME' WHERE id = 347806;
   -- re-run ingest
   -- confirm name reverted to "Mindfulness" via the Garmin API value
   ```

7. **Run the backfill** (dry-run first):
   ```bash
   cd src && PYTHONPATH=. ../.venv/bin/python \
     ../misc_scripts/backfill_lifestyle.py --dry-run | head -50
   ```
   Then real run. Spot-check that an early DayStats day has been
   populated with child rows (both `lifestylelog` and, for QUANTITY
   behaviours actually logged that day, `lifestylelogdetail`).

8. **API smoke tests:**
   ```bash
   curl localhost:9329/api/day/2026-06-06 | jq '.behaviours[] | select(.details | length > 0)'
   curl localhost:9329/api/behaviours | jq
   curl localhost:9329/api/behaviours/347806 | jq '.days[-7:]'
   ```
   The first query specifically isolates behaviours with detail rows
   so multi-detail days like Late Caffeine surface clearly.

9. **Browser smoke test:**
   - Day tab → pick a recent day → behaviours panel populates,
     multi-detail behaviours (e.g. Late Caffeine) show one chip per
     sub-type ("Coffee 1", "Soda 2").
   - Behaviours tab (press `5`) → list of behaviours with yes-rates,
     click one → per-day chart renders.

## Out of scope

- **Aggregate counter** on `DayStats` — explicitly not wanted.
- **Writing** to Garmin (logging behaviours from the dashboard) — the
  library has no write methods and probe showed no obvious endpoints.
- **Per-behaviour category override** — Garmin's `category` field is
  fixed by the behaviour template; the mobile app's "DAYTIME" grouping
  doesn't come down via the API and would require local mapping if we
  wanted it.
- **Behaviours tab implementation** is sketched but can be done in a
  follow-up after the ingestion + Day-view pieces are working.
