"""Audit log + state machine for the notification layer.

Spec: roadmap §13.3. Every dispatched notification — successful or
not — lands in `notifications`. Ack / snooze / rate-limit /
resurface logic all read and write through this module.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Optional

from ..portfolio.store import connect, init_db
from ..strategy.conviction import ConvictionPick
from .notify import NotificationPayload


def record(
    payload: NotificationPayload,
    pick: ConvictionPick,
    backend_results: dict[str, bool],
    ts: Optional[datetime] = None,
) -> int:
    """Persist a dispatch attempt. Returns the row id."""
    init_db()
    when = (ts or datetime.utcnow()).isoformat()
    delivered_ok = any(backend_results.values())
    pick_blob = json.dumps({
        "payload": asdict(payload),
        "gates_passed": list(pick.gates_passed),
        "confidence_band": list(pick.confidence_band),
        "time_to_act": pick.time_to_act.value,
        "idea": {
            "ticker": pick.idea.ticker, "direction": pick.idea.direction,
            "instrument": pick.idea.instrument, "score": pick.idea.score,
            "last_price": pick.idea.last_price,
        },
    }, default=str)
    with connect() as db:
        cur = db.execute(
            """INSERT INTO notifications
               (ts, ticker, score, conviction_pick_json,
                backend_results_json, delivered_ok)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                when, payload.ticker, payload.score,
                pick_blob, json.dumps(backend_results), int(delivered_ok),
            ),
        )
        return int(cur.lastrowid)


def ack(notification_id: int, ts: Optional[datetime] = None) -> bool:
    """Mark a notification acknowledged. No-op + False if not found."""
    init_db()
    when = (ts or datetime.utcnow()).isoformat()
    with connect() as db:
        cur = db.execute(
            "UPDATE notifications SET acked_at = ? WHERE id = ?",
            (when, notification_id),
        )
        return cur.rowcount > 0


def snooze(notification_id: int, hours: float, ts: Optional[datetime] = None) -> bool:
    """Snooze this notification's ticker for `hours`. Returns False if
    the notification row doesn't exist.
    """
    init_db()
    until = (ts or datetime.utcnow()) + timedelta(hours=hours)
    with connect() as db:
        cur = db.execute(
            "UPDATE notifications SET snoozed_until = ? WHERE id = ?",
            (until.isoformat(), notification_id),
        )
        return cur.rowcount > 0


def active_snooze(ticker: str, now: Optional[datetime] = None) -> Optional[dict]:
    """Return the most recent un-expired snooze for this ticker, or None.

    The returned dict carries `id`, `snoozed_until` (datetime), and
    `score` (the score at which the snooze was set — used by the
    resurface-delta check).
    """
    init_db()
    when = (now or datetime.utcnow()).isoformat()
    with connect() as db:
        row = db.execute(
            """SELECT id, snoozed_until, score
               FROM notifications
               WHERE ticker = ? AND snoozed_until IS NOT NULL AND snoozed_until > ?
               ORDER BY id DESC LIMIT 1""",
            (ticker.upper(), when),
        ).fetchone()
    if not row:
        return None
    return {
        "id": int(row["id"]),
        "snoozed_until": datetime.fromisoformat(row["snoozed_until"]),
        "score": float(row["score"]),
    }


def last_dispatch_at(ticker: str) -> Optional[datetime]:
    """Most recent successful dispatch for this ticker. Replaces the
    Cluster 1 proxy that read conviction_log.
    """
    init_db()
    with connect() as db:
        row = db.execute(
            """SELECT ts FROM notifications
               WHERE ticker = ? AND delivered_ok = 1
               ORDER BY id DESC LIMIT 1""",
            (ticker.upper(),),
        ).fetchone()
    return datetime.fromisoformat(row["ts"]) if row else None


def count_today(now: Optional[datetime] = None) -> int:
    """How many successful dispatches today (UTC). Powers `max_per_day`."""
    init_db()
    when = now or datetime.utcnow()
    start = datetime(when.year, when.month, when.day).isoformat()
    with connect() as db:
        row = db.execute(
            """SELECT COUNT(*) AS n FROM notifications
               WHERE ts >= ? AND delivered_ok = 1""",
            (start,),
        ).fetchone()
    return int(row["n"])


def count_ticker_last_week(ticker: str, now: Optional[datetime] = None) -> int:
    """How many successful dispatches for this ticker in the last 7
    days. Powers `max_per_ticker_per_week`.
    """
    init_db()
    cutoff = ((now or datetime.utcnow()) - timedelta(days=7)).isoformat()
    with connect() as db:
        row = db.execute(
            """SELECT COUNT(*) AS n FROM notifications
               WHERE ticker = ? AND ts >= ? AND delivered_ok = 1""",
            (ticker.upper(), cutoff),
        ).fetchone()
    return int(row["n"])


def recent(limit: int = 50) -> list[dict]:
    init_db()
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM notifications ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]
