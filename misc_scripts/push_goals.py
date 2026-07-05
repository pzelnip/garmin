"""Publish the local goals.json into the Neon `goals` table (single id=1 row).

The Goals-tab ladder lives in the DB so it can be updated without a code
deploy or a committed file (which would risk a git-pull merge conflict on the
Pi's daily cron). Edit goals.json locally, then run this to publish it:

    ./scripts/push-goals.sh          # wrapper that sources .envrc for you
    # or, from an already-sourced shell:
    cd src && PYTHONPATH=. ../.venv/bin/python ../misc_scripts/push_goals.py

This WRITES TO PRODUCTION NEON by design — unlike garmin.py, which you should
not run locally. It only ever touches the single goals row (upsert), so it's
safe to re-run.
"""

import json
import os

from sqlmodel import select

from db import Goals, db_session

GOALS_JSON = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "goals.json"
)


def main():
    with open(GOALS_JSON, encoding="utf-8") as fh:
        data = json.load(fh)  # parse first so a malformed file never reaches the DB

    with db_session() as session:
        row = session.exec(select(Goals).order_by(Goals.id)).first()
        if row is None:
            row = Goals(data=data)
        else:
            row.data = data
        session.add(row)
        session.commit()
        phases = len(data.get("phases", []))
        print(f"Pushed goals.json → Neon (row id={row.id}, {phases} phases).")


if __name__ == "__main__":
    main()
