from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


@dataclass
class OrderRequest:
    ticker: str
    instrument: str           # 'equity' | 'call' | 'put'
    side: str                 # 'buy' | 'sell'
    quantity: float
    limit_price: Optional[float] = None
    time_in_force: str = "DAY"
    option_symbol: Optional[str] = None
    option_strike: Optional[float] = None
    option_expiration: Optional[str] = None
    client_order_id: Optional[str] = None
    notes: Optional[str] = None


@dataclass
class OrderResult:
    accepted: bool
    broker_order_id: Optional[str]
    fill_price: Optional[float]
    fill_quantity: Optional[float]
    status: str               # 'filled' | 'pending' | 'rejected' | 'queued_for_review'
    message: str = ""


class Broker(ABC):
    """Abstraction over execution venues. Concrete impls: paper, ibkr, tradier, alpaca.

    The home-use design enforces human-in-the-loop: place_order's default
    behavior is to *queue for review* rather than execute. Implementations that
    auto-execute should require an explicit `auto_approve=True` per order.
    """

    name: str = "abstract"

    @abstractmethod
    def place_order(self, req: OrderRequest, auto_approve: bool = False) -> OrderResult:
        ...

    @abstractmethod
    def positions(self) -> list[dict]:
        ...

    def cancel_order(self, broker_order_id: str) -> bool:  # pragma: no cover
        return False
