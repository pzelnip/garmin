-- Schema migrations, applied in order.
-- This project doesn't use a migration framework (Alembic etc.) — when the
-- DayStats SQLModel class gains new columns, the corresponding ALTER TABLE
-- goes here and gets run manually against the live Postgres DB via
-- ./scripts/db_sess.sh.

-- 2026-05: add hydration columns (water_consumed_ml, water_goal_ml).
ALTER TABLE daystats ADD COLUMN water_consumed_ml INTEGER;
ALTER TABLE daystats ADD COLUMN water_goal_ml INTEGER;

-- 2026-05: add sleep columns (total + 4 stages + score).
ALTER TABLE daystats ADD COLUMN sleep_total_seconds INTEGER;
ALTER TABLE daystats ADD COLUMN sleep_deep_seconds INTEGER;
ALTER TABLE daystats ADD COLUMN sleep_light_seconds INTEGER;
ALTER TABLE daystats ADD COLUMN sleep_rem_seconds INTEGER;
ALTER TABLE daystats ADD COLUMN sleep_awake_seconds INTEGER;
ALTER TABLE daystats ADD COLUMN sleep_score INTEGER;

-- 2026-06: add freeform notes field for day annotations.
ALTER TABLE daystats ADD COLUMN notes TEXT NOT NULL DEFAULT '';

-- 2026-06: add 1-10 self-rated mood score per day. Nullable since it's a
-- manual entry and most existing rows won't have one.
ALTER TABLE daystats ADD COLUMN mood_score INTEGER;

-- 2026-06: drop the StepsToday table — the intra-day "/" progress page that
-- used it has been removed.
DROP TABLE IF EXISTS stepstoday;
