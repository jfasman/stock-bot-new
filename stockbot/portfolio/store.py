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

CREATE TABLE IF NOT EXISTS recommendations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    ticker TEXT NOT NULL,
    instrument TEXT NOT NULL,
    direction TEXT NOT NULL,
    score REAL,
    suggested_weight REAL,
    expected_return REAL,
    return_stdev REAL,
    horizon_days INTEGER,
    stop_price REAL,
    target_price REAL,
    invalidation TEXT,
    config_hash TEXT,
    payload_json TEXT NOT NULL,
    executed INTEGER NOT NULL DEFAULT 0,
    executed_position_id INTEGER,
    FOREIGN KEY(executed_position_id) REFERENCES positions(id)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    event TEXT NOT NULL,
    actor TEXT,
    payload_json TEXT
);

CREATE TABLE IF NOT EXISTS config_snapshots (
    hash TEXT PRIMARY KEY,
    ts TEXT NOT NULL,
    config_yaml TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trade_journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id INTEGER,
    entry_rationale TEXT,
    exit_rationale TEXT,
    post_mortem TEXT,
    tags TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(position_id) REFERENCES positions(id)
);

CREATE TABLE IF NOT EXISTS guardrail_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conviction_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,                       -- ISO datetime of evaluation
    ticker TEXT NOT NULL,
    instrument TEXT NOT NULL,               -- 'equity' | 'call' | 'put' | 'etf'
    direction TEXT NOT NULL,                -- 'long' | 'short'
    score REAL NOT NULL,                    -- idea.score (signed)
    overall_passed INTEGER NOT NULL,        -- 1 iff every gate passed
    score_passed INTEGER NOT NULL,
    factor_agreement_passed INTEGER NOT NULL,
    regime_passed INTEGER NOT NULL,
    setup_validated_passed INTEGER NOT NULL,
    cooldown_passed INTEGER NOT NULL,
    data_quality_passed INTEGER NOT NULL,
    verdicts_json TEXT NOT NULL,            -- {gate: {passed, reason}}
    pick_json TEXT,                         -- ConvictionPick serialized; NULL if rejected
    config_hash TEXT                        -- reproducibility per discipline norm #2
);

CREATE INDEX IF NOT EXISTS idx_conviction_log_ticker_ts
    ON conviction_log(ticker, ts DESC);

CREATE TABLE IF NOT EXISTS setup_performance (
    setup_name TEXT PRIMARY KEY,            -- one row per setup; upsert on regen
    direction TEXT NOT NULL,                -- 'long' | 'short'
    n_trades INTEGER NOT NULL,
    win_rate REAL NOT NULL,                 -- 0..1
    avg_r REAL NOT NULL,                    -- average R per trade
    expectancy REAL NOT NULL,               -- win_rate × avg_win - (1-win_rate) × avg_loss
    sharpe REAL NOT NULL,                   -- annualized Sharpe of trade returns
    last_validated_at TEXT NOT NULL         -- ISO datetime of the run that produced these stats
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,                       -- ISO datetime the notification was attempted
    ticker TEXT NOT NULL,
    score REAL NOT NULL,                    -- idea.score at dispatch (for resurface delta)
    conviction_pick_json TEXT NOT NULL,     -- full pick + payload, for replay
    backend_results_json TEXT NOT NULL,     -- {backend_name: delivered_ok}
    delivered_ok INTEGER NOT NULL,          -- 1 iff at least one backend succeeded
    acked_at TEXT,                          -- NULL until user acknowledges
    snoozed_until TEXT                      -- NULL unless ticker is on snooze
);

CREATE INDEX IF NOT EXISTS idx_notifications_ticker_ts
    ON notifications(ticker, ts DESC);

CREATE TABLE IF NOT EXISTS rebalance_proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,                       -- ISO datetime proposal was generated
    algo TEXT NOT NULL,                     -- 'equal_risk_contribution' | 'vol_target_book' | 'mean_variance_with_views'
    changes_json TEXT NOT NULL,             -- serialized list[WeightChange]
    raw_turnover REAL NOT NULL,             -- pre-cap turnover
    capped_turnover REAL NOT NULL,          -- post-cap turnover actually applied
    feasible INTEGER NOT NULL,              -- 1 iff constraints satisfied
    notes_json TEXT NOT NULL,               -- list[str] — algo notes / fallback explanations
    breaches_json TEXT NOT NULL,            -- list[str] — constraint breach reasons
    status TEXT NOT NULL DEFAULT 'pending', -- 'pending' | 'approved' | 'rejected' | 'expired'
    decided_at TEXT,                        -- NULL until approve/reject
    decided_by TEXT,                        -- 'user' | 'auto'; reserved for future auto-approve
    config_hash TEXT                        -- reproducibility per discipline norm #2
);

CREATE INDEX IF NOT EXISTS idx_rebalance_proposals_ts
    ON rebalance_proposals(ts DESC);
CREATE INDEX IF NOT EXISTS idx_rebalance_proposals_status
    ON rebalance_proposals(status, ts DESC);

-- Roadmap §13.7 Phase B plumbing. The schema lands now so the per-order
-- approval flow is mostly a routing change later; for Cluster 5 the table
-- is populated only by the (deferred) LiveOrderRouter, never the read-only
-- broker. Status transitions:
--   staged → approved → submitted → filled
--   staged → rejected
--   any → errored
CREATE TABLE IF NOT EXISTS pending_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,                       -- ISO datetime the order was staged
    ticker TEXT NOT NULL,
    instrument TEXT NOT NULL,               -- 'equity' (Cluster 5 is equity-only)
    side TEXT NOT NULL,                     -- 'buy' | 'sell'
    quantity REAL NOT NULL,
    limit_price REAL,                       -- NULL = market order
    time_in_force TEXT NOT NULL DEFAULT 'DAY',
    backend TEXT NOT NULL,                  -- 'alpaca' | 'tradier' | 'ibkr'
    recommendation_id INTEGER,              -- FK to recommendations.id when staged from a paper rec
    status TEXT NOT NULL DEFAULT 'staged',  -- staged | approved | rejected | submitted | filled | errored
    decided_at TEXT,
    decided_by TEXT,                        -- 'user' | 'auto' (reserved)
    submitted_at TEXT,
    broker_order_id TEXT,                   -- broker's id once submitted
    filled_at TEXT,
    fill_price REAL,
    fill_quantity REAL,
    error_message TEXT,
    config_hash TEXT,
    FOREIGN KEY(recommendation_id) REFERENCES recommendations(id)
);

CREATE INDEX IF NOT EXISTS idx_pending_orders_status_ts
    ON pending_orders(status, ts DESC);
CREATE INDEX IF NOT EXISTS idx_pending_orders_ticker_ts
    ON pending_orders(ticker, ts DESC);
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
