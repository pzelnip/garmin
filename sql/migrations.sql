-- Schema migrations, applied in order.
-- This project doesn't use a migration framework (Alembic etc.) — when the
-- DayStats / StepsToday SQLModel classes gain new columns, the corresponding
-- ALTER TABLE goes here and gets run manually against the live Postgres DB
-- via ./scripts/db_sess.sh.

-- 2026-05: add hydration columns (water_consumed_ml, water_goal_ml).
ALTER TABLE daystats ADD COLUMN water_consumed_ml INTEGER;
ALTER TABLE daystats ADD COLUMN water_goal_ml INTEGER;
