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
