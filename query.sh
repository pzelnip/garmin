#!/bin/sh
set -e

LAST_SUN=`python -c 'from datetime import datetime,timedelta; today = datetime.now(); print((today - timedelta(days=today.weekday()+1)).strftime("%Y-%m-%d"))'`
echo "From $LAST_SUN"

echo "============================================="

psql $CONN_STR -t -c "SELECT sum(step_count) || ' steps so far' FROM stepentry WHERE day > '$LAST_SUN';"

echo "============================================="

psql $CONN_STR -t -c "SELECT TO_CHAR(day::date, 'mm/dd') || ' - ' || TO_CHAR(step_count, 'fm999G999') || ' :check:' FROM stepentry WHERE day > '$LAST_SUN';"
