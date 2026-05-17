from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import List, Optional


@dataclass
class Lot:
    ticker: str
    quantity: float
    cost_basis: float          # per share
    acquired: date


@dataclass
class Sale:
    ticker: str
    quantity: float
    proceeds_per_share: float
    sold: date
    matched_lot: Optional[Lot] = None


@dataclass
class WashSaleEvent:
    sale: Sale
    replacement_lot: Lot
    disallowed_loss: float
    new_basis_adjustment: float

    def summary(self) -> str:
        return (
            f"WASH SALE: {self.sale.ticker} sold {self.sale.sold} at loss; "
            f"replacement {self.replacement_lot.acquired} disallows ${self.disallowed_loss:.2f}, "
            f"basis adjusted by ${self.new_basis_adjustment:.2f}"
        )


def detect_wash_sales(lots: List[Lot], sales: List[Sale]) -> List[WashSaleEvent]:
    """Identify wash sales per IRS rule: a loss is disallowed if the same
    security is repurchased within 30 days before OR after the sale.

    Conservative: any overlapping lot in that window triggers; we do NOT prorate
    partial replacements. Talk to a tax professional before relying on this.
    """
    events: List[WashSaleEvent] = []
    for s in sales:
        # Only losses matter.
        if not s.matched_lot:
            continue
        loss_per_share = s.matched_lot.cost_basis - s.proceeds_per_share
        if loss_per_share <= 0:
            continue
        window_start = s.sold - timedelta(days=30)
        window_end = s.sold + timedelta(days=30)
        for repl in lots:
            if repl is s.matched_lot or repl.ticker.upper() != s.ticker.upper():
                continue
            if window_start <= repl.acquired <= window_end:
                disallowed = loss_per_share * min(s.quantity, repl.quantity)
                events.append(WashSaleEvent(
                    sale=s,
                    replacement_lot=repl,
                    disallowed_loss=disallowed,
                    new_basis_adjustment=disallowed,
                ))
                break
    return events
