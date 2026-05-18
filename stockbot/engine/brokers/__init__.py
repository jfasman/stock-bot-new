"""Real-broker connections, read-only first (roadmap §13.7).

This subpackage exists because §11 listed "no real broker" as a gap and the
project's non-goal #3 says "no auto-trade real money without human approval."
The architectural answer — not a config flag — is to ship a read-only broker
type that *cannot* place orders. The compiler, not a runtime check, prevents
auto-trade leaks.

Phase A (this cluster): `LiveBroker` Protocol with `account_summary`,
`positions`, `transactions`, `quote`. Concrete impls for Tradier, Alpaca, IBKR.
Phase B (later cluster): a `LiveOrderRouter` wraps a non-read-only Broker
behind a staged/approval workflow. Phase B is *not* built here; the
pending_orders table + dashboard plumbing land in this cluster so Phase B
is mostly a routing change.

Selection:
    >>> from stockbot.engine.brokers import get_live_broker
    >>> broker = get_live_broker(cfg)  # returns None when brokers.live_backend is null
"""
from __future__ import annotations

from .base import (
    AccountSummary,
    BrokerReadOnlyError,
    LiveBroker,
    LivePosition,
    Quote,
    Transaction,
)
from .factory import get_live_broker

__all__ = [
    "AccountSummary",
    "BrokerReadOnlyError",
    "LiveBroker",
    "LivePosition",
    "Quote",
    "Transaction",
    "get_live_broker",
]
