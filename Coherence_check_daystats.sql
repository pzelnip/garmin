-- Coherence check daystats, make sure the data in daystats is consistent
-- with the data in stepentry.
--
--
-- Currently matches nothing
SELECT
    se.id,
    ds.id,
    se.day,
    se.step_count,
    ds.step_count
FROM
    stepentry AS se
    JOIN daystats AS ds ON se.day = ds.day
WHERE
    se.step_count != ds.step_count;

-- Currently matches nothing
SELECT
    se.id,
    ds.id,
    se.day,
    se.step_count,
    ds.step_count
FROM
    stepentry AS se
    JOIN daystats AS ds ON se.day = ds.day
WHERE
    se.goal_met = true
    AND (ds.step_count < ds.daily_step_goal);

-- Currently matches nothing
SELECT
    se.id,
    ds.id,
    se.day,
    se.step_count,
    ds.step_count
FROM
    stepentry AS se
    JOIN daystats AS ds ON se.day = ds.day
WHERE
    se.goal_met = false
    AND (ds.step_count > ds.daily_step_goal);
