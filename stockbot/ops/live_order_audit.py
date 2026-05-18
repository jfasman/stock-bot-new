"""Audit log + status machine for live orders.

Spec: roadmap §13.7 Phase B. This cluster (Phase A) ships only the schema
and the record/list/decide helpers — the Phase B router that drives the
state machine lands later. Keeping the helpers small and the table shape
locked-in now means Phase B is mostly a routing change.

Status machine:
    staged   → approved   → submitted → filled
    staged   → rejected
    any non-terminal → errored

Every transition writes an `audit_log` event (so a Phase A user who wants
to manually stage and approve an order via the dashboard still has a
queryable trail).
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from . import audit
from ..portfolio.store import connect, init_db


_TERMINAL = {"filled", "rejected", "errored"}


def stage(
    *,
    ticker: str,
    side: str,
    quantity: float,
    backend: str,
    instrument: str = "equity",
    limit_price: Optional[float] = None,
    time_in_force: str = "DAY",
    recommendation_id: Optional[int] = None,
    config_hash: Optional[str] = None,
    ts: Optional[datetime] = None,
) -> int:
    """Insert a new `staged` order. Returns the row id.

    Phase A doesn't auto-stage anything — the dashboard / future
    LiveOrderRouter calls this directly.
    """
    init_db()
    when = (ts or datetime.utcnow()).isoformat()
    with connect() as db:
        cur = db.execute(
            """INSERT INTO pending_orders
               (ts, ticker, instrument, side, quantity, limit_price, time_in_force,
                backend, recommendation_id, status, config_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'staged', ?)""",
            (
                when, ticker.upper(), instrument, side.lower(), float(quantity),
                limit_price, time_in_force, backend, recommendation_id, config_hash,
            ),
        )
        order_id = int(cur.lastrowid)
    audit.log_event("live_order.staged", {
        "id": order_id, "ticker": ticker.upper(), "side": side, "qty": quantity,
        "backend": backend, "recommendation_id": recommendation_id,
    })
    return order_id


def approve(order_id: int, *, by: str = "user", ts: Optional[datetime] = None) -> bool:
    """Mark a staged order approved. Returns True on success.

    The actual broker submission (`broker.submit_order(...)`) is the
    LiveOrderRouter's job, not this module's. This function only flips
    state and audits.
    """
    ok = _transition(order_id, from_status="staged", to_status="approved",
                     by=by, ts=ts)
    if ok:
        audit.log_event("live_order.approved", {"id": order_id, "by": by})
    return ok


def reject(order_id: int, *, by: str = "user", ts: Optional[datetime] = None) -> bool:
    ok = _transition(order_id, from_status="staged", to_status="rejected",
                     by=by, ts=ts)
    if ok:
        audit.log_event("live_order.rejected", {"id": order_id, "by": by})
    return ok


def mark_submitted(
    order_id: int, *, broker_order_id: str, ts: Optional[datetime] = None,
) -> bool:
    """Flip approved → submitted and record the broker's order id."""
    init_db()
    when = (ts or datetime.utcnow()).isoformat()
    with connect() as db:
        cur = db.execute(
            """UPDATE pending_orders
               SET status='submitted', submitted_at=?, broker_order_id=?
               WHERE id=? AND status='approved'""",
            (when, broker_order_id, order_id),
        )
        ok = cur.rowcount > 0
    if ok:
        audit.log_event("live_order.submitted", {
            "id": order_id, "broker_order_id": broker_order_id,
        })
    return ok


def mark_filled(
    order_id: int, *, fill_price: float, fill_quantity: float,
    ts: Optional[datetime] = None,
) -> bool:
    init_db()
    when = (ts or datetime.utcnow()).isoformat()
    with connect() as db:
        cur = db.execute(
            """UPDATE pending_orders
               SET status='filled', filled_at=?, fill_price=?, fill_quantity=?
               WHERE id=? AND status='submitted'""",
            (when, float(fill_price), float(fill_quantity), order_id),
        )
        ok = cur.rowcount > 0
    if ok:
        audit.log_event("live_order.filled", {
            "id": order_id, "fill_price": fill_price, "fill_quantity": fill_quantity,
        })
    return ok


def mark_errored(order_id: int, *, message: str, ts: Optional[datetime] = None) -> bool:
    init_db()
    when = (ts or datetime.utcnow()).isoformat()
    with connect() as db:
        # Allow erroring from any non-terminal state.
        cur = db.execute(
            """UPDATE pending_orders
               SET status='errored', error_message=?, decided_at=?
               WHERE id=? AND status NOT IN ('filled', 'rejected', 'errored')""",
            (message, when, order_id),
        )
        ok = cur.rowcount > 0
    if ok:
        audit.log_event("live_order.errored", {"id": order_id, "message": message})
    return ok


def get(order_id: int) -> Optional[dict]:
    init_db()
    with connect() as db:
        row = db.execute(
            "SELECT * FROM pending_orders WHERE id=?", (order_id,),
        ).fetchone()
    return dict(row) if row else None


def staged() -> List[dict]:
    return _list(where="status='staged'")


def recent(limit: int = 50) -> List[dict]:
    return _list(where="1=1", limit=limit)


def _transition(
    order_id: int, *, from_status: str, to_status: str, by: str,
    ts: Optional[datetime],
) -> bool:
    init_db()
    when = (ts or datetime.utcnow()).isoformat()
    with connect() as db:
        cur = db.execute(
            """UPDATE pending_orders
               SET status=?, decided_at=?, decided_by=?
               WHERE id=? AND status=?""",
            (to_status, when, by, order_id, from_status),
        )
        return cur.rowcount > 0


def _list(*, where: str, limit: int = 100) -> List[dict]:
    init_db()
    with connect() as db:
        rows = db.execute(
            f"SELECT * FROM pending_orders WHERE {where} ORDER BY ts DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]
