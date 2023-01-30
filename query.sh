#!/bin/sh
set -e

LAST_SUN=`python last_day_for_weekly_post.py`
echo "From $LAST_SUN"

echo "============================================="

psql $CONN_STR -t -c "SELECT sum(step_count) || ' steps so far' FROM stepentry WHERE day > '$LAST_SUN';"

echo "============================================="

psql $CONN_STR -t -c "SELECT TO_CHAR(day::date, 'mm/dd') || ' - ' || TO_CHAR(step_count, 'fm999G999') || ' :check:' FROM stepentry WHERE day > '$LAST_SUN';"
