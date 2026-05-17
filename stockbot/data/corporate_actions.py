from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional

import pandas as pd

from .vendors import get_vendor


@dataclass
class CorporateAction:
    date: str
    type: str      # 'split' | 'dividend' | 'spinoff' | 'merger'
    value: float


def actions(ticker: str, vendor: Optional[str] = None) -> list[CorporateAction]:
    df = get_vendor(vendor).corporate_actions(ticker)
    if df is None or df.empty:
        return []
    out: list[CorporateAction] = []
    for _, row in df.iterrows():
        out.append(CorporateAction(date=str(row["date"]), type=str(row["type"]), value=float(row["value"])))
    return out


def as_of(ticker: str, asof: str | date, vendor: Optional[str] = None) -> list[CorporateAction]:
    """Return corporate actions strictly before or on `asof`. Used by the backtester
    to enforce point-in-time discipline.
    """
    asof_str = str(asof)
    return [a for a in actions(ticker, vendor) if a.date <= asof_str]


def split_adjust(price: float, ticker: str, asof: str | date, vendor: Optional[str] = None) -> float:
    """Adjust a historical price back to today's basis by walking forward splits."""
    factor = 1.0
    asof_str = str(asof)
    for a in actions(ticker, vendor):
        if a.type == "split" and a.date > asof_str and a.value > 0:
            factor *= a.value
    return price / factor if factor != 0 else price


def filter_delisted(tickers: Iterable[str], vendor: Optional[str] = None) -> list[str]:
    """Return tickers the vendor reports as still listed. Soft check — yfinance
    returns empty history for many delisted names but is not authoritative.
    """
    v = get_vendor(vendor)
    return [t for t in tickers if not v.is_delisted(t)]
