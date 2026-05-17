from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .broker import OrderRequest


@dataclass
class Quote:
    bid: float
    ask: float

    @property
    def mid(self) -> float:
        if self.bid > 0 and self.ask > 0:
            return (self.bid + self.ask) / 2.0
        return max(self.bid, self.ask)

    @property
    def spread(self) -> float:
        return max(0.0, self.ask - self.bid)


def limit_from_book(quote: Quote, side: str, aggression: float = 0.5) -> float:
    """Compute a limit price from the current book.

    `aggression` is in [0,1]: 0 = passive (post at the better side), 1 = aggressive
    (cross the spread). 0.5 = midpoint.
    """
    aggression = max(0.0, min(1.0, aggression))
    if side == "buy":
        return round(quote.bid + aggression * quote.spread, 2)
    return round(quote.ask - aggression * quote.spread, 2)


def build_equity_order(
    ticker: str, side: str, quantity: float, quote: Quote, aggression: float = 0.5,
    client_order_id: Optional[str] = None, notes: Optional[str] = None,
) -> OrderRequest:
    return OrderRequest(
        ticker=ticker.upper(),
        instrument="equity",
        side=side,
        quantity=quantity,
        limit_price=limit_from_book(quote, side, aggression),
        client_order_id=client_order_id,
        notes=notes,
    )


def build_option_order(
    underlying: str,
    option_type: str,
    strike: float,
    expiration: str,
    side: str,
    quantity: float,
    quote: Quote,
    aggression: float = 0.5,
    option_symbol: Optional[str] = None,
    client_order_id: Optional[str] = None,
    notes: Optional[str] = None,
) -> OrderRequest:
    return OrderRequest(
        ticker=underlying.upper(),
        instrument=option_type,
        side=side,
        quantity=quantity,
        limit_price=limit_from_book(quote, side, aggression),
        option_symbol=option_symbol,
        option_strike=strike,
        option_expiration=expiration,
        client_order_id=client_order_id,
        notes=notes,
    )
