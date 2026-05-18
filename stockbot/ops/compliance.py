"""Required pre-checks for any live order (roadmap §13.7).

Two rules — PDT (`pdt.evaluate`) and wash-sale (`wash_sale.detect_wash_sales`)
— exist as opt-in utilities elsewhere in `ops/`. The roadmap promotes both
to *required pre-checks* for any live order regardless of `compliance.*`
toggles. The toggles continue to govern paper reporting; this module is the
hard gate for the live path.

Phase A doesn't actually submit live orders — but Phase B will, and we want
the contract locked in now so the routing change at that point is trivial.

A failure here is a *hard reject*: there is no override path. A future
cluster may add one with the multi-step ack pattern from `ops/guardrails.py`,
but Cluster 5 deliberately does not.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional

from . import pdt, wash_sale


@dataclass(frozen=True)
class ComplianceCheck:
    """Result of the required pre-check.

    ``ok=False`` means the live order must NOT be submitted. ``reasons`` lists
    every rule that failed (the caller surfaces these to the user / audit log).
    """
    ok: bool
    reasons: List[str] = field(default_factory=list)
    pdt_status: Optional[pdt.PDTStatus] = None
    wash_sale_events: List[wash_sale.WashSaleEvent] = field(default_factory=list)


def pre_check_live_order(
    *,
    intent: str,                                # 'open' | 'close'
    ticker: str,
    intraday_close: bool,                       # would closing this position today be a day trade?
    account_equity: float,
    recent_round_trips: List[pdt.RoundTrip],
    open_lots: List[wash_sale.Lot],
    proposed_sale: Optional[wash_sale.Sale] = None,
    today: Optional[date] = None,
) -> ComplianceCheck:
    """Run the two required checks for a live order.

    - PDT: when ``intraday_close=True``, run the FINRA 4-day-trades-in-5
      check. Failing means the broker would restrict the intraday trade
      anyway; we surface the rejection ourselves so the audit captures it.
    - Wash sale: when the order is a closing sale at a loss, detect whether
      a replacement lot inside the 30-day window would disallow the loss.
      We don't auto-cancel for tax reasons — but we *do* refuse to silently
      eat the disallowed loss without flagging.
    """
    reasons: List[str] = []
    pdt_status: Optional[pdt.PDTStatus] = None
    wash_events: List[wash_sale.WashSaleEvent] = []

    if intraday_close:
        pdt_status = pdt.evaluate(
            account_equity=account_equity,
            recent_round_trips=recent_round_trips,
            today=today,
        )
        if not pdt_status.can_open_intraday:
            reasons.append(
                f"PDT rule blocks intraday close on {ticker.upper()}: "
                f"{pdt_status.note or 'account equity below 25k with 4+ day trades.'}"
            )

    if intent == "close" and proposed_sale is not None:
        # Detect against existing open lots — the replacement window check
        # uses the same `detect_wash_sales` rule the paper reporter uses.
        wash_events = wash_sale.detect_wash_sales(open_lots, [proposed_sale])
        if wash_events:
            total_disallowed = sum(e.disallowed_loss for e in wash_events)
            reasons.append(
                f"Wash-sale rule would disallow ${total_disallowed:,.2f} of "
                f"loss on {ticker.upper()} (replacement lot within 30 days)."
            )

    return ComplianceCheck(
        ok=not reasons,
        reasons=reasons,
        pdt_status=pdt_status,
        wash_sale_events=wash_events,
    )
