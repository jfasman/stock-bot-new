from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from ..portfolio.store import connect, init_db


@dataclass
class JournalEntry:
    position_id: int
    entry_rationale: str
    exit_rationale: Optional[str] = None
    post_mortem: Optional[str] = None
    tags: Optional[str] = None


def record_entry(position_id: int, entry_rationale: str, tags: Optional[str] = None) -> None:
    init_db()
    with connect() as db:
        db.execute(
            """INSERT INTO trade_journal (position_id, entry_rationale, tags, updated_at)
               VALUES (?, ?, ?, ?)""",
            (position_id, entry_rationale, tags, datetime.utcnow().isoformat()),
        )


def record_exit(position_id: int, exit_rationale: str, post_mortem: Optional[str] = None) -> None:
    init_db()
    with connect() as db:
        existing = db.execute(
            "SELECT id FROM trade_journal WHERE position_id = ? ORDER BY id DESC LIMIT 1",
            (position_id,),
        ).fetchone()
        ts = datetime.utcnow().isoformat()
        if existing:
            db.execute(
                """UPDATE trade_journal SET exit_rationale = ?, post_mortem = ?, updated_at = ?
                   WHERE id = ?""",
                (exit_rationale, post_mortem, ts, existing["id"]),
            )
        else:
            db.execute(
                """INSERT INTO trade_journal (position_id, entry_rationale, exit_rationale,
                                              post_mortem, updated_at)
                   VALUES (?, '', ?, ?, ?)""",
                (position_id, exit_rationale, post_mortem, ts),
            )


def get_entry(position_id: int) -> Optional[dict]:
    with connect() as db:
        row = db.execute(
            "SELECT * FROM trade_journal WHERE position_id = ? ORDER BY id DESC LIMIT 1",
            (position_id,),
        ).fetchone()
    return dict(row) if row else None


def all_entries(limit: int = 100) -> list[dict]:
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM trade_journal ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]
