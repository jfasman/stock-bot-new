"""Side-by-side: paper portfolio vs the connected live brokerage book.

Spec: roadmap §13.7 "Reconciliation" — for any ticker held in both, show
paper P&L vs. live P&L, sized differences, and any divergence in entry /
exit timing. The empirical answer to "would my system have done better?"

The paper book is the control, the live book is the experiment, both
running on the same recommendations.

This module is pure: it takes already-fetched paper positions and live
positions and produces a comparison. The fetches happen in the caller
(CLI / dashboard), not here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..engine.brokers.base import LivePosition
from ..portfolio.portfolio import Position


@dataclass(frozen=True)
class PositionComparison:
    """One ticker's paper-vs-live state.

    All fields are optional because a ticker may exist in only one book —
    paper_only and live_only sides are first-class comparisons, not errors.
    """
    ticker: str
    paper_quantity: float = 0.0
    paper_avg_entry: float = 0.0
    paper_unrealized_pnl: float = 0.0
    paper_unrealized_pct: float = 0.0
    live_quantity: float = 0.0
    live_avg_entry: float = 0.0
    live_unrealized_pnl: Optional[float] = None
    live_unrealized_pct: Optional[float] = None
    quantity_delta: float = 0.0          # live − paper (positive = live is bigger)
    pnl_delta: Optional[float] = None     # live − paper unrealized

    @property
    def status(self) -> str:
        """Where this ticker exists: 'both' | 'paper_only' | 'live_only'."""
        in_paper = abs(self.paper_quantity) > 1e-9
        in_live = abs(self.live_quantity) > 1e-9
        if in_paper and in_live:
            return "both"
        if in_paper:
            return "paper_only"
        return "live_only"


@dataclass(frozen=True)
class LiveVsPaperReport:
    rows: List[PositionComparison]
    paper_total_unrealized: float = 0.0
    live_total_unrealized: Optional[float] = None
    notes: List[str] = field(default_factory=list)

    @property
    def both_count(self) -> int:
        return sum(1 for r in self.rows if r.status == "both")

    @property
    def paper_only_count(self) -> int:
        return sum(1 for r in self.rows if r.status == "paper_only")

    @property
    def live_only_count(self) -> int:
        return sum(1 for r in self.rows if r.status == "live_only")


def compare(
    paper_positions: List[Position],
    live_positions: List[LivePosition],
    *,
    paper_marks: Optional[Dict[str, float]] = None,
) -> LiveVsPaperReport:
    """Build the side-by-side report.

    ``paper_marks`` provides current mark prices for paper positions so the
    unrealized P&L lines up with the live broker's reported numbers. When
    absent, paper P&L is computed against entry_price (zero).
    """
    paper_marks = paper_marks or {}

    # Group paper positions by ticker (a paper book can have multiple
    # positions per ticker — sum them for the comparison row).
    paper_agg: Dict[str, Dict[str, float]] = {}
    for p in paper_positions:
        if p.instrument != "equity":
            continue        # Cluster 5 is equity-only — see roadmap "Out of scope"
        key = p.ticker.upper()
        bucket = paper_agg.setdefault(key, {"qty": 0.0, "cost": 0.0, "pnl": 0.0, "cost_basis": 0.0})
        bucket["qty"] += p.quantity
        bucket["cost"] += p.quantity * p.entry_price
        mark = paper_marks.get(p.ticker, p.entry_price)
        bucket["pnl"] += p.unrealized_pnl(mark)
        bucket["cost_basis"] += p.cost_basis

    live_by_ticker: Dict[str, LivePosition] = {p.ticker.upper(): p for p in live_positions}

    tickers = sorted(set(paper_agg) | set(live_by_ticker))
    rows: List[PositionComparison] = []
    paper_total = 0.0
    live_total: Optional[float] = 0.0

    for t in tickers:
        p = paper_agg.get(t)
        l = live_by_ticker.get(t)

        if p:
            p_qty = p["qty"]
            p_avg = p["cost"] / p_qty if p_qty else 0.0
            p_pnl = p["pnl"]
            p_pct = (p_pnl / p["cost_basis"]) if p["cost_basis"] else 0.0
            paper_total += p_pnl
        else:
            p_qty = p_avg = p_pnl = p_pct = 0.0

        if l:
            l_qty = l.quantity
            l_avg = l.avg_entry_price
            l_pnl = l.unrealized_pnl
            l_pct = l.unrealized_pnl_pct
            if l_pnl is not None and live_total is not None:
                live_total += l_pnl
            elif l_pnl is None:
                live_total = None  # poisons the sum — one missing value invalidates the total
        else:
            l_qty = l_avg = 0.0
            l_pnl = l_pct = None

        rows.append(PositionComparison(
            ticker=t,
            paper_quantity=p_qty, paper_avg_entry=p_avg,
            paper_unrealized_pnl=p_pnl, paper_unrealized_pct=p_pct,
            live_quantity=l_qty, live_avg_entry=l_avg,
            live_unrealized_pnl=l_pnl, live_unrealized_pct=l_pct,
            quantity_delta=l_qty - p_qty,
            pnl_delta=(l_pnl - p_pnl) if l_pnl is not None else None,
        ))

    notes: List[str] = []
    paper_only = [r.ticker for r in rows if r.status == "paper_only"]
    live_only = [r.ticker for r in rows if r.status == "live_only"]
    if paper_only:
        notes.append(
            f"In paper but not live: {', '.join(paper_only)} — "
            f"system would have entered but the live account did not."
        )
    if live_only:
        notes.append(
            f"In live but not paper: {', '.join(live_only)} — "
            f"manual positions or executions outside the system."
        )

    return LiveVsPaperReport(
        rows=rows,
        paper_total_unrealized=paper_total,
        live_total_unrealized=live_total,
        notes=notes,
    )
