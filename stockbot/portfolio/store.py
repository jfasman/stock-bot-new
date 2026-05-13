from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from ..config import DB_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    instrument TEXT NOT NULL,           -- 'equity' | 'call' | 'put'
    direction TEXT NOT NULL,            -- 'long' | 'short'
    quantity REAL NOT NULL,             -- shares or contracts
    entry_price REAL NOT NULL,
    entry_date TEXT NOT NULL,
    stop_price REAL,
    target_price REAL,
    option_symbol TEXT,
    option_strike REAL,
    option_expiration TEXT,
    status TEXT NOT NULL DEFAULT 'open',-- 'open' | 'closed'
    exit_price REAL,
    exit_date TEXT,
    realized_pnl REAL,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id INTEGER,
    ts TEXT NOT NULL,
    action TEXT NOT NULL,               -- 'open' | 'close'
    ticker TEXT NOT NULL,
    instrument TEXT NOT NULL,
    quantity REAL NOT NULL,
    price REAL NOT NULL,
    cash_after REAL NOT NULL,
    notes TEXT,
    FOREIGN KEY(position_id) REFERENCES positions(id)
);

CREATE TABLE IF NOT EXISTS portfolio (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    cash REAL NOT NULL,
    starting_cash REAL NOT NULL,
    started_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS equity_curve (
    ts TEXT PRIMARY KEY,
    equity REAL NOT NULL
);
"""


@contextmanager
def connect(path: Path | None = None):
    db = sqlite3.connect(path or DB_PATH)
    db.row_factory = sqlite3.Row
    try:
        yield db
        db.commit()
    finally:
        db.close()


def init_db(path: Path | None = None) -> None:
    with connect(path) as db:
        db.executescript(_SCHEMA)
