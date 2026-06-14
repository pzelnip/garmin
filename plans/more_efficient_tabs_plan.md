# Plan: Per-Tab On-Demand Data Fetching for the Garmin Dashboard

## Context

Today every `/dashboard` page load calls `build_dashboard_data()`
([dashboard_data.py:255-692](src/dashboard_data.py#L255-L692)) which does **one**
`get_all_entries()` query and then computes data for **all five tabs at once**
(Steps, Water, Sleep, Mood, Day) — even though the user only sees one tab at a
time. The full payload is embedded into the page: stat panels render
server-side via Jinja from `data.stats.*`, and chart data is dumped inline as
`const charts = {{ charts_json | safe }}`
([dashboard.jinja2:2022](src/dashboard.jinja2#L2022)).

The goal: make each tab a **minimal request/response cycle** that fetches only
that tab's data on click, with a **frontend cache** so re-visiting a tab in the
same session does not re-fetch. The Day tab already works this way
(`GET /api/day/<iso_date>` + `ensureDayLoaded()` at
[dashboard.jinja2:3758](src/dashboard.jinja2#L3758)) — we generalize that
pattern to the other four tabs.

## Approach (decisions confirmed with the user)

- **Server-rendered HTML fragments.** Each tab endpoint returns
  `{html, charts}`: the tab's existing Jinja block, extracted verbatim into a
  partial and rendered server-side, plus that tab's chart data as JSON. The JS
  swaps `innerHTML` then runs the existing per-tab chart init. This is a
  near-verbatim cut of current markup — lowest regression risk, no rewrite of
  the stat panels/macros/tables.
- **Initial load = subtitle + eager steps tab.** `/dashboard` renders the
  always-visible subtitle and eagerly loads the default (steps) tab so the page
  isn't blank on first paint. The other three tabs fetch on first click.
- **Keep the 5-minute server-side cache TTL** as a backstop, plus explicit
  invalidation on notes/mood writes. The frontend cache means each tab is
  fetched at most once per page load anyway.

### Why not pure-JSON + client rendering
The stat panels are macro-heavy server-side Jinja (83 `data.*` references; macros
`pass_fail_strip` [1154](src/dashboard.jinja2#L1154), `dow_best_footer`
[1166](src/dashboard.jinja2#L1166), `weekly_comparison_panel`
[1178](src/dashboard.jinja2#L1178), `mood_bucket`
[1883](src/dashboard.jinja2#L1883); three tables at
[1503](src/dashboard.jinja2#L1503)/[1520](src/dashboard.jinja2#L1520)/[1536](src/dashboard.jinja2#L1536)).
Reimplementing all of that in JS (à la `renderDay`) is a large rewrite with high
visual-regression risk and no payoff for a single-user app.

### Why not htmx (considered, rejected for this codebase)
htmx maps onto this almost exactly: tab buttons become
`<button hx-get="/api/tab/steps" hx-target="#panel-steps"
hx-trigger="click once" hx-swap="innerHTML">`. `hx-trigger="click once"` gives
the frontend cache for free (fires only the first time), and `hx-target`/
`hx-swap` replace the manual `innerHTML` swap — the endpoint can then return raw
HTML instead of `{html, charts}` JSON. It's ~14KB from a CDN, no build step,
and fits the project's CDN-script convention.

It is rejected because the part that's actually non-trivial here is **chart
re-init**, which htmx cannot abstract. The swapped fragment is inert until
Chart.js instantiates against its `<canvas>` elements using the per-tab `charts`
data. With htmx that data still has to reach JS after the swap (via an inline
`<script>` in the fragment, or an `htmx:afterSwap` listener reading an embedded
JSON blob) and call the existing `initStepsTab(...)` etc. That glue is about the
same volume of code as the hand-rolled `ensureTabLoaded` it would replace — htmx
relocates the JS rather than eliminating it. For exactly four fragment endpoints
with a mandatory chart-reinit step, the hand-rolled `fetch` approach (~15 lines,
mirroring the existing `renderDay`/`ensureDayLoaded` pattern) keeps a single
mental model. htmx would win if there were many dynamic fragments, server-driven
partial updates, or inline edits/polling — but the charts undercut its biggest
payoff here.

## Changes

### 1. `src/dashboard_data.py` — decompose the monolith

Every tab builder needs the full ordered entries list (all-time DoW averages,
sparklines, weekly comparisons). So cache the **entries list** process-wide and
pass it into each builder — preserving the single-query property while letting a
request compute only one tab.

**No extra DB queries from the split.** This is the key correctness requirement,
not just an optimization. Two separate caches do two different jobs:
- `_ENTRIES_CACHE` holds the raw `DayStats` rows — shared across all tabs, so the
  DB is hit at most **once per TTL window regardless of how many tabs are
  clicked** (same query count as today's monolith).
- `_TAB_CACHE` holds each tab's computed aggregate dict — saves re-aggregating in
  Python on re-visit.

Concretely: clicking Water runs the DB query and caches the rows; clicking Sleep
next is an `_ENTRIES_CACHE` **hit** (no query) and only re-runs the in-memory
aggregation. Every builder MUST pull rows via `get_entries_cached()` and accept
`entries` as a parameter — a builder that calls `get_all_entries()` directly
silently reintroduces a per-tab full-table SELECT and defeats this.

Add:

```python
def get_entries_cached() -> list[DayStats]:
    # cached get_all_entries(include_notes=False), 300s TTL

def build_shared_data(entries) -> dict:
    # subtitle scalars only: total_days, total_steps, hyd_total_liters,
    # sleep_total_hours

def build_steps_data(entries) -> dict   # steps stats + streaks + tables + steps charts
def build_water_data(entries) -> dict   # hyd_* stats + hyd_* charts
def build_sleep_data(entries) -> dict   # sleep_* stats + sleep_* charts
def build_mood_data(entries) -> dict    # mood_* stats + mood_* charts
```

Each builder is a **cut-and-redistribute** of [dashboard_data.py:255-692](src/dashboard_data.py#L255-L692)
by metric prefix. Reuse the existing helpers unchanged: `_build_dow_averages`,
`_build_dow_best`, `_build_weekly_comparison`, `_build_heatmap`,
`_build_step_histogram`, `_serialize_step_days`, `_serialize_streak`,
`_build_current_streak_payload`, `rolling_avg`, `build_streaks`,
`find_current_streak`.

Each builder **returns the same dict shape** as the relevant slice of today's
payload (e.g. steps builder returns `{"stats": {...steps keys...},
"current_streak", "top_streaks", "top_step_days", "bottom_step_days",
"charts": {...steps charts...}}`) so the fragment templates need **zero** key
renames.

### 2. `src/dashboard_data.py` — per-tab caching

Replace the single `_CACHE` payload ([dashboard_data.py:695-737](src/dashboard_data.py#L695-L737)):

```python
_ENTRIES_CACHE = {"data": None, "built_at": None}     # raw entries, 300s TTL
_TAB_CACHE = {"steps": None, "water": None, "sleep": None, "mood": None}
_SHARED_CACHE = {"data": None, "built_at": None}

def get_tab_data_cached(tab: str) -> dict        # build from get_entries_cached() if stale
def get_shared_data_cached() -> dict             # subtitle scalars

def invalidate_dashboard_cache():
    # clear _ENTRIES_CACHE + all _TAB_CACHE entries + _SHARED_CACHE
```

Keep the function **name and signature** `invalidate_dashboard_cache()` so the
existing callers in app.py ([219](src/app.py#L219), [250](src/app.py#L250)) are
untouched. Notes/mood writes clear everything — simplest correct behavior (mood
edits shift `mood_*` stats; clearing all is cheap).

### 3. Template restructuring — split into partials

The macros are shared across tabs, so move them to a shared file. Create under
`src/`:

- `_macros.jinja2` — move `pass_fail_strip`/`dow_best_footer`/`weekly_comparison_panel`
  ([1154-1222](src/dashboard.jinja2#L1154-L1222)). (`mood_bucket` at
  [1883](src/dashboard.jinja2#L1883) is mood-only — keep it in the mood fragment.)
- `_tab_steps.jinja2` — verbatim cut of the steps `tab-panel`
  ([1278-1546](src/dashboard.jinja2#L1278-L1546), including the tables grid).
- `_tab_water.jinja2` — [1552-1662](src/dashboard.jinja2#L1552-L1662) (keep the
  `{% if data.stats.hyd_total_days %}` empty-state guard).
- `_tab_sleep.jinja2` — [1665-1763](src/dashboard.jinja2#L1665-L1763).
- `_tab_mood.jinja2` — [1770-1934](src/dashboard.jinja2#L1770-L1934).

Each fragment starts with `{% from "_macros.jinja2" import pass_fail_strip,
dow_best_footer, weekly_comparison_panel %}` and references the same
`data.stats.*` / `data.charts.*` variables as today.

In `dashboard.jinja2`, the four **metric** tab-panels become empty shells the JS
fills (keep `active` on steps for the initial-tab fallback):

```html
<div class="tab-panel active" data-tab="steps"></div>
<div class="tab-panel" data-tab="water"></div>
<div class="tab-panel" data-tab="sleep"></div>
<div class="tab-panel" data-tab="mood"></div>
```

The subtitle ([1250](src/dashboard.jinja2#L1250)) and the **Day** tab
([1940-2011](src/dashboard.jinja2#L1940-L2011)) stay in the base template
unchanged. Change `const charts = {{ charts_json | safe }}`
([2022](src/dashboard.jinja2#L2022)) to `let charts = {};` (mutable, filled
per tab).

### 4. `src/app.py` — routes

Add the per-tab endpoint and slim down `/dashboard`
([85-96](src/app.py#L85-L96)):

```python
@app.route("/api/tab/<tab>")
def tab_data(tab):
    if tab not in ("steps", "water", "sleep", "mood"):
        return jsonify({"error": "unknown tab"}), 404
    data = get_tab_data_cached(tab)
    html = render_template(f"_tab_{tab}.jinja2", data=data)
    return jsonify({"html": html, "charts": data["charts"]})


@app.route("/dashboard")
def dashboard():
    git_sha, git_commit_date = _git_info()
    return render_template(
        "dashboard.jinja2",
        data={"stats": get_shared_data_cached()},   # subtitle only
        git_sha=git_sha,
        git_commit_date=git_commit_date,
        diagnostics=_diagnostics(),
    )
```

Update imports ([12](src/app.py#L12)) to pull `get_tab_data_cached`,
`get_shared_data_cached`, `invalidate_dashboard_cache`. Drop `charts_json` and
`get_dashboard_data_cached`.

### 5. `src/dashboard.jinja2` — frontend fetch + cache + render-then-init

Mirror the `dayLoadedFor` pattern ([3757-3763](src/dashboard.jinja2#L3757-L3763)).
The per-tab chart init functions (`initStepsTab` [2133](src/dashboard.jinja2#L2133),
`initWaterTab` [2701](src/dashboard.jinja2#L2701), `initSleepTab`
[2816](src/dashboard.jinja2#L2816), `initMoodTab` [3047](src/dashboard.jinja2#L3047))
read from the module-level `charts` object — leave them unchanged. Add a fetch +
cache layer in front of `ensureTabInitialized`:

```js
let charts = {};                                  // was const
const tabHtmlLoaded = { steps:false, water:false, sleep:false, mood:false };

async function ensureTabLoaded(name) {
    if (name === 'day') { ensureDayLoaded(); return; }
    if (tabHtmlLoaded[name]) return;              // frontend cache: no re-fetch
    const panel = document.querySelector(`.tab-panel[data-tab="${name}"]`);
    const res = await fetch(`/api/tab/${name}`);
    const { html, charts: tabCharts } = await res.json();
    panel.innerHTML = html;
    Object.assign(charts, tabCharts);
    tabHtmlLoaded[name] = true;
    ensureTabInitialized(name);                   // existing fn builds Chart.js instances
}
```

Make `activateTab` ([3451](src/dashboard.jinja2#L3451)) `async`: replace the
synchronous `ensureTabInitialized(name)` ([3454](src/dashboard.jinja2#L3454))
with `await ensureTabLoaded(name)`, and run the chart resize/replay loop
([3465-3471](src/dashboard.jinja2#L3465-L3471)) **after** the await. Keep
`localStorage.setItem` ([3474](src/dashboard.jinja2#L3474)), the Day branch
([3475](src/dashboard.jinja2#L3475)), hotkeys
([3484-3492](src/dashboard.jinja2#L3484-L3492)), and the stored-tab restore
([3998-4010](src/dashboard.jinja2#L3998-L4010)). `tabHtmlLoaded` gates the fetch
and `tabInitState` ([3437](src/dashboard.jinja2#L3437)) still prevents double
chart init.

On initial paint, the stored/default tab activation already fires through
`activateTab`, so steps loads eagerly via `ensureTabLoaded` — no extra wiring.

## Implementation order

1. `dashboard_data.py`: add `get_entries_cached`; split `build_dashboard_data`
   into `build_shared_data` + four per-tab builders (identical return shapes).
2. `dashboard_data.py`: replace `_CACHE` with `_ENTRIES_CACHE` / `_TAB_CACHE` /
   `_SHARED_CACHE`; add `get_tab_data_cached` / `get_shared_data_cached`; update
   `invalidate_dashboard_cache`.
3. Templates: create `_macros.jinja2` and `_tab_{steps,water,sleep,mood}.jinja2`
   as verbatim cuts.
4. `dashboard.jinja2`: empty the four metric tab-panels; change `const charts =`
   to `let charts = {};`.
5. `app.py`: add `/api/tab/<tab>`; slim `/dashboard`; fix imports.
6. `dashboard.jinja2` JS: add `tabHtmlLoaded` + `ensureTabLoaded`; make
   `activateTab` async and await before the resize/replay loop.

## Verification

Run locally against the prod Neon DB (read-only is safe — no `garmin.py`):

```bash
set -a; source ./.envrc; set +a
GARMIN_DASHBOARD_DEBUG=1 ./.venv/bin/python src/app.py
# http://localhost:9329/dashboard
```

In the browser (DevTools Network tab open):

- **Initial load**: page renders subtitle + steps tab; exactly one
  `GET /api/tab/steps`.
- **Tab switch** (click or hotkeys `1`–`5`): each of water/sleep/mood fetches
  `/api/tab/<name>` **once**; Day still uses `/api/day/<date>`.
- **Re-visit a tab**: switch away and back → **no** new request (frontend cache).
- **Visual parity**: each tab matches current output — headline panels, insight
  cards, pass/fail strips, weekly-comparison panels, tables, and all charts
  render identically.
- **Cache invalidation**: on the Day tab, edit a mood/notes value (PUT fires),
  reload the page, open the Mood tab → reflects the edit (server cache cleared
  by `invalidate_dashboard_cache`).
- **Empty states**: confirm the `hyd_total_days` / `sleep_total_days` /
  `mood_total_days` guards still render their empty-state markup when a metric
  has no data.

No tests to run (personal project convention). Verification is browser-based.

## Alternative considered: precompute-on-sync (materialized tab payloads)

Documented for a possible follow-up — **not** part of the change above. The
recommended plan stays compute-on-demand because it delivers the per-tab +
frontend-cache win with the least code and risk, and the data is bounded today.

**The insight.** All dashboard data except the Day tab is *write-rarely,
read-often*: it changes once a day (the Garmin sync via `garmin.py --auto`,
[scripts/run-garmin.sh](scripts/run-garmin.sh)) plus occasional mood/notes edits.
Computing aggregates on every page view — even cached behind a 5-min TTL — is
recomputing data whose inputs change once a day. The TTL cache is a crude
time-based *approximation* of "don't recompute between syncs"; precomputing does
it exactly, keyed on the actual write events.

**The shape.** Move the per-tab aggregation out of the request path and into
**sync time**: when `garmin.py` finishes a sync, it builds the tab payloads and
writes them to a persistent layer. The UI request becomes a single keyed *read*
of a finished payload — no aggregation under user latency, no full-table list
resident in the Flask process (resolves the in-memory concern from a different
angle).

This is a distinct third option, not the SQL-`GROUP BY` hybrid: the hybrid still
computes on demand (just in SQL); this computes on write. It reuses the exact
`build_steps_data`/`build_water_data`/… builders from the recommended plan — only
*when* and *where* they run changes.

**Where the payload lives.** A small Postgres table in the same Neon DB, e.g.
`dashboard_cache(tab text primary key, payload_json jsonb, built_at timestamptz)`
— one `CREATE TABLE` appended to [sql/migrations.sql](sql/migrations.sql), no new
infra, shared by both Pi and Mac. (A disk file doesn't span the two-machine
setup; in-process loses the benefit on restart.)

**Triggers / invalidation** move from time-based to write-based:
- Daily Garmin sync → recompute all tab payloads (steps/water/sleep/mood).
- Mood/notes edit ([app.py:219](src/app.py#L219), [250](src/app.py#L250)) →
  recompute the mood payload (and shared subtitle). The existing
  `invalidate_dashboard_cache()` hook becomes "rebuild" instead of "clear."
- Day tab stays on-demand and live (`/api/day/<date>`) — it's inherently per-day.

**Required caveats:**
- **Compute-on-miss fallback** is mandatory: first deploy, a not-yet-run sync, or
  a cold table must fall back to building the payload on request (and storing it).
  This makes the materialized layer an *optimization over* the recommended plan,
  not a replacement — the on-demand builders stay as the fallback path.
- **Local dev**: the Mac reads prod read-only and must not run `garmin.py`, so it
  never triggers a recompute — it reads whatever the Pi last materialized, or
  hits the compute-on-miss fallback. The fallback resolves this cleanly.

**What scales and what doesn't (carried over from the caching discussion).**
Materializing helps memory uniformly (no fat ORM objects held; only finished
payloads), but the *stored payload size* still splits two ways: the aggregate
portions (scalars, DoW, monthly, histogram, weekly) are constant-size and flat as
the DB grows; the per-day chart series (timeline, cumulative, heatmap, sparkline,
scatter) grow linearly with history regardless of where they're stored, because
the charts plot every day. Precompute changes *when* that cost is paid, not its
growth curve — to bound the per-day series you'd have to change what the UI shows
(windowing/downsampling old data).

**When to pick this up:** if recompute-on-request latency becomes noticeable, if
the dataset stops being one-row-per-day (sub-daily samples), or if you simply want
work aligned to the once-a-day change rate. Until then the recommended plan is the
right size.
