from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from .store import connect, init_db


@dataclass
class Position:
    id: int
    ticker: str
    instrument: str
    direction: str
    quantity: float
    entry_price: float
    entry_date: str
    stop_price: Optional[float]
    target_price: Optional[float]
    option_symbol: Optional[str]
    option_strike: Optional[float]
    option_expiration: Optional[str]
    status: str
    exit_price: Optional[float]
    exit_date: Optional[str]
    realized_pnl: Optional[float]
    notes: Optional[str]

    @property
    def cost_basis(self) -> float:
        multiplier = 100.0 if self.instrument in ("call", "put") else 1.0
        return self.quantity * self.entry_price * multiplier

    def market_value(self, current_price: float) -> float:
        multiplier = 100.0 if self.instrument in ("call", "put") else 1.0
        return self.quantity * current_price * multiplier

    def unrealized_pnl(self, current_price: float) -> float:
        sign = 1.0 if self.direction == "long" else -1.0
        return sign * (current_price - self.entry_price) * self.quantity * (
            100.0 if self.instrument in ("call", "put") else 1.0
        )


def _row_to_position(row) -> Position:
    return Position(**{k: row[k] for k in row.keys()})


class Portfolio:
    """Virtual portfolio backed by SQLite. Multiplier-aware for options."""

    def __init__(self, starting_cash: float = 100_000.0):
        init_db()
        self.starting_cash = starting_cash
        self._ensure_seeded()

    def _ensure_seeded(self) -> None:
        with connect() as db:
            row = db.execute("SELECT * FROM portfolio WHERE id = 1").fetchone()
            if row is None:
                db.execute(
                    "INSERT INTO portfolio (id, cash, starting_cash, started_at) VALUES (1, ?, ?, ?)",
                    (self.starting_cash, self.starting_cash, datetime.utcnow().isoformat()),
                )

    @property
    def cash(self) -> float:
        with connect() as db:
            row = db.execute("SELECT cash FROM portfolio WHERE id = 1").fetchone()
            return float(row["cash"]) if row else 0.0

    @property
    def started_at(self) -> str:
        with connect() as db:
            row = db.execute("SELECT started_at FROM portfolio WHERE id = 1").fetchone()
            return str(row["started_at"]) if row else datetime.utcnow().isoformat()

    def list_open(self) -> List[Position]:
        with connect() as db:
            rows = db.execute("SELECT * FROM positions WHERE status = 'open'").fetchall()
        return [_row_to_position(r) for r in rows]

    def list_closed(self) -> List[Position]:
        with connect() as db:
            rows = db.execute(
                "SELECT * FROM positions WHERE status = 'closed' ORDER BY id DESC"
            ).fetchall()
        return [_row_to_position(r) for r in rows]

    def equity(self, mark_prices: dict[str, float]) -> float:
        total = self.cash
        for p in self.list_open():
            key = p.option_symbol or p.ticker
            price = mark_prices.get(key)
            if price is None:
                price = p.entry_price  # fallback
            total += p.market_value(price)
        return float(total)

    def record_equity(self, equity_value: float) -> None:
        with connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO equity_curve (ts, equity) VALUES (?, ?)",
                (datetime.utcnow().isoformat(), float(equity_value)),
            )

    def equity_curve(self) -> list[tuple[str, float]]:
        with connect() as db:
            rows = db.execute("SELECT ts, equity FROM equity_curve ORDER BY ts").fetchall()
        return [(r["ts"], float(r["equity"])) for r in rows]

    def open_position(
        self,
        ticker: str,
        instrument: str,
        direction: str,
        quantity: float,
        entry_price: float,
        stop_price: float | None = None,
        target_price: float | None = None,
        option_symbol: str | None = None,
        option_strike: float | None = None,
        option_expiration: str | None = None,
        notes: str | None = None,
    ) -> int:
        multiplier = 100.0 if instrument in ("call", "put") else 1.0
        cost = quantity * entry_price * multiplier
        with connect() as db:
            cash = db.execute("SELECT cash FROM portfolio WHERE id = 1").fetchone()["cash"]
            if cost > cash:
                raise ValueError(f"Insufficient cash: need {cost:.2f}, have {cash:.2f}")
            new_cash = cash - cost
            now = datetime.utcnow().isoformat()
            cur = db.execute(
                """INSERT INTO positions
                   (ticker, instrument, direction, quantity, entry_price, entry_date,
                    stop_price, target_price, option_symbol, option_strike,
                    option_expiration, status, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)""",
                (
                    ticker, instrument, direction, quantity, entry_price, now,
                    stop_price, target_price, option_symbol, option_strike,
                    option_expiration, notes,
                ),
            )
            position_id = int(cur.lastrowid)
            db.execute("UPDATE portfolio SET cash = ? WHERE id = 1", (new_cash,))
            db.execute(
                """INSERT INTO trades (position_id, ts, action, ticker, instrument,
                                       quantity, price, cash_after, notes)
                   VALUES (?, ?, 'open', ?, ?, ?, ?, ?, ?)""",
                (position_id, now, ticker, instrument, quantity, entry_price, new_cash, notes),
            )
        return position_id

    def close_position(self, position_id: int, exit_price: float, notes: str | None = None) -> float:
        with connect() as db:
            row = db.execute(
                "SELECT * FROM positions WHERE id = ? AND status = 'open'", (position_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"No open position with id {position_id}")
            pos = _row_to_position(row)
            multiplier = 100.0 if pos.instrument in ("call", "put") else 1.0
            sign = 1.0 if pos.direction == "long" else -1.0
            proceeds = pos.quantity * exit_price * multiplier
            realized = sign * (exit_price - pos.entry_price) * pos.quantity * multiplier
            cash = db.execute("SELECT cash FROM portfolio WHERE id = 1").fetchone()["cash"]
            new_cash = cash + proceeds
            now = datetime.utcnow().isoformat()
            db.execute(
                """UPDATE positions SET status = 'closed', exit_price = ?, exit_date = ?,
                   realized_pnl = ?, notes = COALESCE(?, notes) WHERE id = ?""",
                (exit_price, now, realized, notes, position_id),
            )
            db.execute("UPDATE portfolio SET cash = ? WHERE id = 1", (new_cash,))
            db.execute(
                """INSERT INTO trades (position_id, ts, action, ticker, instrument,
                                       quantity, price, cash_after, notes)
                   VALUES (?, ?, 'close', ?, ?, ?, ?, ?, ?)""",
                (position_id, now, pos.ticker, pos.instrument, pos.quantity,
                 exit_price, new_cash, notes),
            )
        return float(realized)
