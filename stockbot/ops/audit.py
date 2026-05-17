from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from ..portfolio.store import connect, init_db


def log_event(event: str, payload: Optional[dict] = None, actor: str = "system") -> int:
    """Append a structured audit event. Every state-changing action should call this."""
    init_db()
    blob = json.dumps(payload or {}, default=str)
    with connect() as db:
        cur = db.execute(
            "INSERT INTO audit_log (ts, event, actor, payload_json) VALUES (?, ?, ?, ?)",
            (datetime.utcnow().isoformat(), event, actor, blob),
        )
        return int(cur.lastrowid)


def recent_events(limit: int = 100, event_like: Optional[str] = None) -> list[dict]:
    with connect() as db:
        if event_like:
            rows = db.execute(
                "SELECT * FROM audit_log WHERE event LIKE ? ORDER BY id DESC LIMIT ?",
                (event_like, limit),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["payload"] = json.loads(d.get("payload_json") or "{}")
        except Exception:
            d["payload"] = {}
        out.append(d)
    return out
