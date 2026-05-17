from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from typing import List, Optional

from ..portfolio.store import connect, init_db
from ..strategy.thesis import Thesis


def log_thesis(thesis: Thesis) -> int:
    """Persist a Thesis for later attribution analysis. Every recommendation
    (executed or not) MUST be logged — this is the audit trail.
    """
    init_db()
    payload = json.dumps(asdict(thesis), default=str)
    with connect() as db:
        cur = db.execute(
            """INSERT INTO recommendations
               (ts, ticker, instrument, direction, score, suggested_weight,
                expected_return, return_stdev, horizon_days, stop_price, target_price,
                invalidation, config_hash, payload_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                thesis.created_at, thesis.ticker, thesis.instrument, thesis.direction,
                thesis.score, thesis.suggested_weight, thesis.expected_return,
                thesis.return_stdev, thesis.horizon_days, thesis.stop_price, thesis.target_price,
                "; ".join(thesis.invalidation_triggers), thesis.config_hash, payload,
            ),
        )
        return int(cur.lastrowid)


def mark_executed(rec_id: int, position_id: int) -> None:
    with connect() as db:
        db.execute(
            "UPDATE recommendations SET executed = 1, executed_position_id = ? WHERE id = ?",
            (position_id, rec_id),
        )


def recent(limit: int = 50) -> list[dict]:
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM recommendations ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def hit_rate(window: int = 200) -> dict:
    """How many of the most recent recommendations were executed, and the average score."""
    rows = recent(window)
    if not rows:
        return {"executed": 0, "total": 0, "rate": 0.0, "avg_score": 0.0}
    executed = sum(1 for r in rows if r["executed"])
    avg = sum((r["score"] or 0.0) for r in rows) / len(rows)
    return {
        "executed": executed,
        "total": len(rows),
        "rate": executed / len(rows),
        "avg_score": avg,
    }
