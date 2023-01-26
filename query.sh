#!/bin/sh
set -e

LAST_SUN=`python -c 'from datetime import datetime,timedelta; today = datetime.now(); print((today - timedelta(days=today.weekday()+1)).strftime("%Y-%m-%d"))'`
echo "From $LAST_SUN"

echo "============================================="

echo "select sum(step_count) || ' steps so far' from stepentry where day > '$LAST_SUN';" | sqlite3 database.db

echo "============================================="

echo "select STRFTIME('%m/%d', day) || ' - ' || printf('%,d', step_count) || ' :check:' FROM stepentry WHERE day > '$LAST_SUN';" | sqlite3 database.db
