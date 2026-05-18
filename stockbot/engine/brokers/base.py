"""Protocol + value objects for live-broker read-only connections.

`LiveBroker` is deliberately a *separate* type from `engine.broker.Broker`.
Where `Broker` exposes `place_order`, `LiveBroker` does not — the absence is
the type-level guarantee that a read-only connection cannot place trades.

`BrokerReadOnlyError` exists for the defensive case where code somehow tries
to coerce a `LiveBroker` into placing an order anyway (e.g. via duck typing).
Raising it is preferable to silently degrading. Phase B's `LiveOrderRouter`
catches this error when the user has not yet enabled the per-order approval
flow.

All four methods are read-only by design. Implementations live in sibling
modules (`tradier.py`, `alpaca.py`, `ibkr.py`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import List, Optional, Protocol, runtime_checkable


class BrokerReadOnlyError(RuntimeError):
    """Raised when something tries to place an order through a read-only broker.

    A LiveBroker has no `place_order`/`cancel_order` methods by design — the
    compiler enforces read-only. This error covers the residual case where a
    caller invokes one via reflection or duck typing.
    """


@dataclass(frozen=True)
class AccountSummary:
    """Snapshot of a real brokerage account's high-level state.

    Currency-agnostic in field names; impls report whatever the broker returns.
    Field names mirror what the dashboard's Live tab renders.
    """
    account_id: str
    cash: float                      # settled cash
    buying_power: float              # broker's computed buying power
    equity: float                    # cash + positions market value
    last_equity: Optional[float]     # previous close equity, when available
    currency: str = "USD"
    status: str = "ACTIVE"           # broker's account status string
    pdt: Optional[bool] = None       # True if pattern-day-trader flagged
    as_of: Optional[datetime] = None


@dataclass(frozen=True)
class LivePosition:
    """One real brokerage position. Equity-only for Cluster 5 — options on a
    live broker are deferred (see roadmap §13.7 'Out of scope')."""
    ticker: str
    quantity: float
    avg_entry_price: float
    current_price: Optional[float]
    market_value: Optional[float]
    unrealized_pnl: Optional[float]
    unrealized_pnl_pct: Optional[float]
    side: str = "long"               # 'long' | 'short'


@dataclass(frozen=True)
class Transaction:
    """One executed transaction at the live broker."""
    ts: datetime
    ticker: str
    side: str                        # 'buy' | 'sell'
    quantity: float
    price: float
    fees: float = 0.0
    transaction_id: Optional[str] = None
    raw: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Quote:
    """A point-in-time quote. Bid/ask optional — some endpoints return only last."""
    ticker: str
    last: float
    bid: Optional[float] = None
    ask: Optional[float] = None
    as_of: Optional[datetime] = None

    @property
    def mid(self) -> Optional[float]:
        if self.bid is not None and self.ask is not None:
            return 0.5 * (self.bid + self.ask)
        return self.last


@runtime_checkable
class LiveBroker(Protocol):
    """Read-only view onto a real brokerage account.

    No `place_order` / `cancel_order` here by design — see module docstring.
    Implementations should be defensive: any vendor 4xx/5xx surfaces as an
    exception, not as a silently-wrong empty result.
    """

    name: str

    def account_summary(self) -> AccountSummary: ...

    def positions(self) -> List[LivePosition]: ...

    def transactions(self, since: date) -> List[Transaction]: ...

    def quote(self, symbol: str) -> Quote: ...
