from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import List


@dataclass
class RoundTrip:
    ticker: str
    open_date: date
    close_date: date


@dataclass
class PDTStatus:
    account_equity: float
    day_trades_last_5: int
    flagged_as_pdt: bool
    can_open_intraday: bool
    note: str


def is_day_trade(open_date: date, close_date: date) -> bool:
    return open_date == close_date


def evaluate(
    account_equity: float,
    recent_round_trips: List[RoundTrip],
    today: date | None = None,
) -> PDTStatus:
    """FINRA Pattern Day Trader rule: 4+ day trades in any 5 business days flags
    you as PDT, requiring $25k minimum equity. Below $25k with PDT status, the
    broker will restrict intraday trading.
    """
    today = today or date.today()
    cutoff = today - timedelta(days=7)   # rough business-week proxy
    dts = [r for r in recent_round_trips if r.close_date >= cutoff and is_day_trade(r.open_date, r.close_date)]
    n_dt = len(dts)
    flagged = n_dt >= 4 and account_equity < 25_000
    can_open = not flagged
    note = ""
    if account_equity < 25_000 and n_dt >= 3:
        note = "1 more day trade in the rolling 5-day window will flag PDT."
    if flagged:
        note = "PDT status: intraday trading restricted until equity >= $25k."
    return PDTStatus(
        account_equity=account_equity,
        day_trades_last_5=n_dt,
        flagged_as_pdt=flagged,
        can_open_intraday=can_open,
        note=note,
    )
