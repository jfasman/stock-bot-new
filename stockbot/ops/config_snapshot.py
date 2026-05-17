from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Optional

import yaml

from ..config import Config
from ..portfolio.store import connect, init_db


def hash_config(cfg: Config) -> str:
    """Stable hash of the raw config dict. Used to tag every recommendation."""
    serialized = yaml.safe_dump(cfg.raw, sort_keys=True)
    return hashlib.sha256(serialized.encode()).hexdigest()[:16]


def snapshot(cfg: Config) -> str:
    """Persist a snapshot of the current config keyed by hash. Idempotent —
    re-snapshotting the same config is a no-op.
    """
    init_db()
    h = hash_config(cfg)
    serialized = yaml.safe_dump(cfg.raw, sort_keys=True)
    with connect() as db:
        exists = db.execute("SELECT 1 FROM config_snapshots WHERE hash = ?", (h,)).fetchone()
        if not exists:
            db.execute(
                "INSERT INTO config_snapshots (hash, ts, config_yaml) VALUES (?, ?, ?)",
                (h, datetime.utcnow().isoformat(), serialized),
            )
    return h


def get_snapshot(hash_: str) -> Optional[dict]:
    with connect() as db:
        row = db.execute("SELECT * FROM config_snapshots WHERE hash = ?", (hash_,)).fetchone()
    return dict(row) if row else None
