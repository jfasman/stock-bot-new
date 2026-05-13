from __future__ import annotations

import time
from functools import lru_cache
from typing import Optional

import pandas as pd

try:
    import yfinance as yf
except ImportError:  # pragma: no cover - dependency installed via requirements.txt
    yf = None


_PRICE_TTL_SECONDS = 60 * 5
_price_cache: dict[tuple[str, str], tuple[float, pd.DataFrame]] = {}


def _cache_get(key: tuple[str, str]) -> Optional[pd.DataFrame]:
    hit = _price_cache.get(key)
    if not hit:
        return None
    ts, df = hit
    if time.time() - ts > _PRICE_TTL_SECONDS:
        return None
    return df


def get_history(ticker: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
    """Return OHLCV history for `ticker`. Cached for 5 minutes per (ticker, period)."""
    if yf is None:
        raise RuntimeError("yfinance is not installed. Run: pip install -r requirements.txt")
    key = (ticker.upper(), f"{period}:{interval}")
    cached = _cache_get(key)
    if cached is not None:
        return cached.copy()
    df = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=False)
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.rename(columns=str.title)
    _price_cache[key] = (time.time(), df)
    return df.copy()


def get_last_price(ticker: str) -> Optional[float]:
    df = get_history(ticker, period="5d", interval="1d")
    if df.empty:
        return None
    return float(df["Close"].iloc[-1])


@lru_cache(maxsize=256)
def get_info(ticker: str) -> dict:
    if yf is None:
        return {}
    try:
        return yf.Ticker(ticker).info or {}
    except Exception:
        return {}
