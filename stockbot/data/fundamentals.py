from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from .vendors import get_vendor


_TTL_SECONDS = 60 * 60 * 6  # fundamentals change slowly; cache 6h
_cache: dict[str, tuple[float, dict]] = {}


@dataclass
class Fundamentals:
    ticker: str
    sector: Optional[str] = None
    industry: Optional[str] = None
    market_cap: Optional[float] = None
    trailing_pe: Optional[float] = None
    forward_pe: Optional[float] = None
    price_to_book: Optional[float] = None
    ev_to_ebitda: Optional[float] = None
    fcf_yield: Optional[float] = None
    gross_margin: Optional[float] = None
    operating_margin: Optional[float] = None
    profit_margin: Optional[float] = None
    return_on_equity: Optional[float] = None
    return_on_assets: Optional[float] = None
    debt_to_equity: Optional[float] = None
    current_ratio: Optional[float] = None
    beta: Optional[float] = None
    short_percent_of_float: Optional[float] = None
    shares_short: Optional[float] = None
    earnings_growth: Optional[float] = None
    revenue_growth: Optional[float] = None
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "Fundamentals":
        known = {f for f in cls.__dataclass_fields__ if f != "raw"}
        return cls(**{k: d.get(k) for k in known}, raw=d)


def get_fundamentals(ticker: str, vendor: Optional[str] = None) -> Fundamentals:
    key = f"{(vendor or 'default').lower()}::{ticker.upper()}"
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < _TTL_SECONDS:
        return Fundamentals.from_dict(hit[1])
    raw = get_vendor(vendor).fundamentals(ticker)
    _cache[key] = (time.time(), raw)
    return Fundamentals.from_dict(raw or {"ticker": ticker.upper()})
