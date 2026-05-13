from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

import pandas as pd

try:
    import yfinance as yf
except ImportError:  # pragma: no cover
    yf = None


@dataclass
class OptionContract:
    symbol: str
    underlying: str
    expiration: str        # YYYY-MM-DD
    strike: float
    option_type: str       # 'call' | 'put'
    bid: float
    ask: float
    last: float
    volume: int
    open_interest: int
    implied_vol: float
    in_the_money: bool

    @property
    def mid(self) -> float:
        if self.bid > 0 and self.ask > 0:
            return round((self.bid + self.ask) / 2, 2)
        return self.last

    @property
    def dte(self) -> int:
        try:
            exp = datetime.strptime(self.expiration, "%Y-%m-%d").date()
            return max(0, (exp - datetime.utcnow().date()).days)
        except Exception:
            return 0


def _safe_float(value, default: float = 0.0) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    if f != f:  # NaN check
        return default
    return f


def _safe_int(value, default: int = 0) -> int:
    return int(_safe_float(value, float(default)))


def _row_to_contract(row: pd.Series, underlying: str, expiration: str, opt_type: str) -> OptionContract:
    return OptionContract(
        symbol=str(row.get("contractSymbol", "") or ""),
        underlying=underlying,
        expiration=expiration,
        strike=_safe_float(row.get("strike")),
        option_type=opt_type,
        bid=_safe_float(row.get("bid")),
        ask=_safe_float(row.get("ask")),
        last=_safe_float(row.get("lastPrice")),
        volume=_safe_int(row.get("volume")),
        open_interest=_safe_int(row.get("openInterest")),
        implied_vol=_safe_float(row.get("impliedVolatility")),
        in_the_money=bool(row.get("inTheMoney", False)),
    )


def list_expirations(ticker: str) -> List[str]:
    if yf is None:
        return []
    try:
        return list(yf.Ticker(ticker).options or [])
    except Exception:
        return []


def get_chain(ticker: str, expiration: str) -> tuple[List[OptionContract], List[OptionContract]]:
    """Return (calls, puts) for a given expiration."""
    if yf is None:
        return [], []
    try:
        chain = yf.Ticker(ticker).option_chain(expiration)
    except Exception:
        return [], []
    calls = [_row_to_contract(r, ticker, expiration, "call") for _, r in chain.calls.iterrows()]
    puts = [_row_to_contract(r, ticker, expiration, "put") for _, r in chain.puts.iterrows()]
    return calls, puts


def pick_contract(
    ticker: str,
    direction: str,                  # 'call' or 'put'
    spot: float,
    dte_min: int,
    dte_max: int,
    moneyness: float = 0.0,          # 0 = ATM, positive = OTM, negative = ITM (in % of spot)
) -> Optional[OptionContract]:
    """Pick a reasonable contract within the DTE window closest to the desired strike."""
    target_strike = spot * (1 + moneyness if direction == "call" else 1 - moneyness)
    best: Optional[OptionContract] = None
    best_distance = float("inf")
    for exp in list_expirations(ticker):
        try:
            days = (datetime.strptime(exp, "%Y-%m-%d").date() - datetime.utcnow().date()).days
        except Exception:
            continue
        if days < dte_min or days > dte_max:
            continue
        calls, puts = get_chain(ticker, exp)
        candidates = calls if direction == "call" else puts
        for c in candidates:
            if c.bid <= 0 and c.ask <= 0 and c.last <= 0:
                continue
            distance = abs(c.strike - target_strike)
            if distance < best_distance:
                best = c
                best_distance = distance
    return best
