from __future__ import annotations

from datetime import datetime
from typing import Optional

from ..portfolio.portfolio import Portfolio
from .broker import Broker, OrderRequest, OrderResult


class PaperBroker(Broker):
    """Paper broker that fills at the order's limit price (or rejects)."""

    name = "paper"

    def __init__(self, portfolio: Portfolio):
        self.portfolio = portfolio

    def place_order(self, req: OrderRequest, auto_approve: bool = False) -> OrderResult:
        if not auto_approve:
            return OrderResult(
                accepted=True,
                broker_order_id=None,
                fill_price=None,
                fill_quantity=None,
                status="queued_for_review",
                message="awaiting manual approval",
            )
        if req.limit_price is None or req.limit_price <= 0:
            return OrderResult(False, None, None, None, "rejected", "missing limit price")
        try:
            if req.side == "buy":
                pid = self.portfolio.open_position(
                    ticker=req.ticker,
                    instrument=req.instrument,
                    direction="long",
                    quantity=req.quantity,
                    entry_price=req.limit_price,
                    option_symbol=req.option_symbol,
                    option_strike=req.option_strike,
                    option_expiration=req.option_expiration,
                    notes=req.notes,
                )
                return OrderResult(True, f"paper-{pid}", req.limit_price, req.quantity, "filled")
            # sell — only supports closing existing positions in this paper impl.
            for pos in self.portfolio.list_open():
                if pos.ticker == req.ticker and pos.instrument == req.instrument:
                    realized = self.portfolio.close_position(pos.id, req.limit_price, notes=req.notes)
                    return OrderResult(True, f"paper-{pos.id}", req.limit_price, pos.quantity, "filled",
                                       message=f"realized {realized:+.2f}")
            return OrderResult(False, None, None, None, "rejected", "no matching open position")
        except ValueError as exc:
            return OrderResult(False, None, None, None, "rejected", str(exc))

    def positions(self) -> list[dict]:
        return [
            {
                "ticker": p.ticker, "instrument": p.instrument, "direction": p.direction,
                "quantity": p.quantity, "entry_price": p.entry_price,
                "option_symbol": p.option_symbol,
            }
            for p in self.portfolio.list_open()
        ]
