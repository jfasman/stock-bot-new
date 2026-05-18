"""Audit log + approval workflow for book-level rebalance proposals.

Spec: roadmap §13.8 "Both write to a new `rebalance_proposals` table — same
audit pattern as `recommendations`. The user approves or rejects; only
approved proposals translate to orders."

The pure rebalancer lives in `portfolio/rebalancer.py`. This module is the
persistence and workflow shell — record a proposal, list pending, approve,
reject. Translating an approved proposal into actual paper trades is the
engine's job (in `engine/paper.py`), not this module's.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from typing import List, Optional

from ..portfolio.rebalancer import RebalanceProposal, WeightChange
from ..portfolio.store import connect, init_db


def _proposal_to_jsonable(proposal: RebalanceProposal) -> dict:
    """Stable on-disk shape — used for the changes_json column."""
    return {
        "algo": proposal.algo,
        "changes": [asdict(c) for c in proposal.changes],
        "raw_turnover": proposal.raw_turnover,
        "capped_turnover": proposal.capped_turnover,
        "feasible": proposal.feasible,
        "notes": list(proposal.notes),
        "constraint_breaches": list(proposal.constraint_breaches),
    }


def record(
    proposal: RebalanceProposal,
    *,
    ts: Optional[datetime] = None,
    config_hash: Optional[str] = None,
) -> int:
    """Insert a new proposal as ``status='pending'`` and return its row id."""
    init_db()
    when = (ts or datetime.utcnow()).isoformat()
    changes_json = json.dumps([asdict(c) for c in proposal.changes])
    notes_json = json.dumps(list(proposal.notes))
    breaches_json = json.dumps(list(proposal.constraint_breaches))
    with connect() as db:
        cur = db.execute(
            """INSERT INTO rebalance_proposals
               (ts, algo, changes_json, raw_turnover, capped_turnover,
                feasible, notes_json, breaches_json, status, config_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
            (
                when,
                proposal.algo,
                changes_json,
                proposal.raw_turnover,
                proposal.capped_turnover,
                int(proposal.feasible),
                notes_json,
                breaches_json,
                config_hash,
            ),
        )
        return int(cur.lastrowid)


def approve(proposal_id: int, *, ts: Optional[datetime] = None, by: str = "user") -> bool:
    """Mark a pending proposal approved. Returns True on success."""
    init_db()
    when = (ts or datetime.utcnow()).isoformat()
    with connect() as db:
        cur = db.execute(
            """UPDATE rebalance_proposals
               SET status='approved', decided_at=?, decided_by=?
               WHERE id=? AND status='pending'""",
            (when, by, proposal_id),
        )
        return cur.rowcount > 0


def reject(proposal_id: int, *, ts: Optional[datetime] = None, by: str = "user") -> bool:
    """Mark a pending proposal rejected. Returns True on success."""
    init_db()
    when = (ts or datetime.utcnow()).isoformat()
    with connect() as db:
        cur = db.execute(
            """UPDATE rebalance_proposals
               SET status='rejected', decided_at=?, decided_by=?
               WHERE id=? AND status='pending'""",
            (when, by, proposal_id),
        )
        return cur.rowcount > 0


def pending() -> List[dict]:
    """Pending proposals, newest first."""
    return _list(where="status='pending'")


def recent(limit: int = 20) -> List[dict]:
    """All proposals (any status), newest first, capped at `limit`."""
    return _list(where="1=1", limit=limit)


def get(proposal_id: int) -> Optional[dict]:
    init_db()
    with connect() as db:
        row = db.execute(
            "SELECT * FROM rebalance_proposals WHERE id=?",
            (proposal_id,),
        ).fetchone()
    return _row_to_dict(row) if row else None


def _list(*, where: str, limit: int = 100) -> List[dict]:
    init_db()
    with connect() as db:
        rows = db.execute(
            f"SELECT * FROM rebalance_proposals WHERE {where} "
            f"ORDER BY ts DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def _row_to_dict(row) -> dict:
    d = dict(row)
    # Decode JSON columns for callers — they shouldn't have to repeat this.
    for col in ("changes_json", "notes_json", "breaches_json"):
        raw = d.get(col)
        if raw:
            try:
                d[col[:-5]] = json.loads(raw)  # strip "_json" suffix
            except json.JSONDecodeError:
                d[col[:-5]] = []
    return d
