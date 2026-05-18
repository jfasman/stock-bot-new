"""Audit log for the conviction gate. Spec: roadmap §13.1 "Logging".

Every call to `conviction.evaluate` writes one row here — pass *or*
fail — so the gate verdicts we did not surface are queryable. This
is the analogue of `recommendations` for the alert layer.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from typing import Optional

from ..portfolio.store import connect, init_db
from ..strategy.conviction import ConvictionPick, GateResult
from ..strategy.ideas import Idea


_PER_GATE_COLUMNS = (
    "score",
    "factor_agreement",
    "regime",
    "setup_validated",
    "cooldown",
    "data_quality",
)


def log_evaluation(
    idea: Idea,
    verdicts: dict[str, GateResult],
    pick: ConvictionPick | None,
    ts: datetime | None = None,
    config_hash: str | None = None,
) -> int:
    """Persist one evaluation. Returns the row id."""
    init_db()
    when = (ts or datetime.utcnow()).isoformat()
    overall = pick is not None
    verdicts_payload = {
        name: {"passed": v.passed, "reason": v.reason} for name, v in verdicts.items()
    }
    pick_payload = json.dumps(_pick_to_jsonable(pick), default=str) if pick else None
    per_gate = [int(verdicts[name].passed) for name in _PER_GATE_COLUMNS]
    with connect() as db:
        cur = db.execute(
            """INSERT INTO conviction_log
               (ts, ticker, instrument, direction, score, overall_passed,
                score_passed, factor_agreement_passed, regime_passed,
                setup_validated_passed, cooldown_passed, data_quality_passed,
                verdicts_json, pick_json, config_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                when, idea.ticker, idea.instrument, idea.direction, idea.score,
                int(overall), *per_gate,
                json.dumps(verdicts_payload), pick_payload, config_hash,
            ),
        )
        return int(cur.lastrowid)


def last_notified_at(ticker: str) -> Optional[datetime]:
    """Most recent successful notification dispatch for this ticker.

    Reads from the `notifications` table (Cluster 3). Falls back to
    "gate passed" rows in `conviction_log` only if `notifications`
    has no row at all for this ticker — preserves the Cluster-1-only
    cooldown behavior for setups that haven't run a full watch
    cycle yet.
    """
    from . import notification_log
    ts = notification_log.last_dispatch_at(ticker)
    if ts is not None:
        return ts
    init_db()
    with connect() as db:
        row = db.execute(
            """SELECT ts FROM conviction_log
               WHERE ticker = ? AND overall_passed = 1
               ORDER BY id DESC LIMIT 1""",
            (ticker.upper(),),
        ).fetchone()
    return datetime.fromisoformat(row["ts"]) if row else None


def recent(limit: int = 50) -> list[dict]:
    init_db()
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM conviction_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def _pick_to_jsonable(pick: ConvictionPick) -> dict:
    d = asdict(pick)
    d["time_to_act"] = pick.time_to_act.value
    return d
