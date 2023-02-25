#!/bin/sh
set -e

LAST_SUN=`python last_day_for_weekly_post.py`
echo "From $LAST_SUN"

echo "============================================="

psql $CONN_STR -t -c "SELECT sum(step_count) || ' steps so far' FROM daystats WHERE day > '$LAST_SUN';"

echo "============================================="

psql $CONN_STR -t -c "SELECT TO_CHAR(day::date, 'mm/dd') || ' - ' || TO_CHAR(step_count, 'fm999G999') || ' :check:' FROM daystats WHERE day > '$LAST_SUN';"

echo "============================================="

psql $CONN_STR -t -c "SELECT day || ',' || (weight_grams * 0.00220462) FROM daystats WHERE day >= '$LAST_SUN';"
