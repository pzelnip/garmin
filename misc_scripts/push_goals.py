"""DB write for the Goals-tab ladder — the single-row Neon `goals` table.

`push()` upserts the ladder JSON. It's the publish step behind `make edit-goals`
(misc_scripts/edit_goals.py), which validates + auto-fixes goals.json on every
save and calls this to make the change live. See CLAUDE.md's Goals-tab section.
"""

from sqlmodel import select

from db import Goals, db_session


def push(data):
    """Upsert the single goals row with `data`. Returns (row_id, n_phases)."""
    with db_session() as session:
        row = session.exec(select(Goals).order_by(Goals.id)).first()
        if row is None:
            row = Goals(data=data)
        else:
            row.data = data
        session.add(row)
        session.commit()
        return row.id, len(data.get("phases", []))
