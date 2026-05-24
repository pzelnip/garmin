-- average BMI, weight by month
SELECT
    date_part('year', day) as year,
    date_part('month', day) as month,
    date_part('month', day) || '/' || date_part('year', day) as month_year,
    -- avg(bmi) as avg_bmi,
    -- avg(weight_grams) as avg_weight_grams,
    avg(weight_grams) * 0.00220462 as avg_weight_pounds
FROM
    daystats
GROUP BY
    date_part('month', day),
    date_part('year', day)
ORDER BY
    year,
    month;

-------------------------------
-- Find all daystats that don't have a source of garmin:
SELECT
    id,
    day,
    source
FROM
    daystats
WHERE
    source != 'garmin';

--------------------------------
-- find all gaps (missing entries) in daystats, useful for backfilling:
WITH missing AS (
  SELECT d::date AS day
  FROM generate_series(
    (SELECT MIN(day) FROM daystats),
    CURRENT_DATE - 1,
    interval '1 day'
  ) AS d
  WHERE NOT EXISTS (SELECT 1 FROM daystats ds WHERE ds.day = d::date)
),
grouped AS (
  SELECT day,
         day - (ROW_NUMBER() OVER (ORDER BY day))::int AS grp
  FROM missing
)
SELECT MIN(day) AS gap_start, MAX(day) AS gap_end, COUNT(*) AS days
FROM grouped
GROUP BY grp
ORDER BY gap_start;
