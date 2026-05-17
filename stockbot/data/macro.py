from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

import pandas as pd

from .vendors import get_vendor

log = logging.getLogger(__name__)

_TTL_SECONDS = 60 * 60  # macro: cache 1h
_cache: dict[str, tuple[float, object]] = {}


# Map of canonical symbol -> yfinance ticker. Easy to swap to FRED later.
_YF_PROXIES = {
    "vix": "^VIX",
    "vix3m": "^VIX3M",
    "vvix": "^VVIX",
    "spx": "^GSPC",
    "us_10y": "^TNX",      # 10y yield index (×10 of actual %)
    "us_2y": "^IRX",       # 13-week T-bill yield, used as short-end proxy
    "us_30y": "^TYX",
    "dxy": "DX-Y.NYB",
}


@dataclass
class MacroSnapshot:
    vix: Optional[float]
    vix_3m: Optional[float]
    yield_2y: Optional[float]
    yield_10y: Optional[float]
    yield_30y: Optional[float]
    dxy: Optional[float]
    spx_close: Optional[float]
    yield_curve_2s10s: Optional[float]   # 10y - 2y, in basis points
    vix_term_structure: Optional[float]  # vix3m / vix

    @property
    def regime_flags(self) -> list[str]:
        flags = []
        if self.vix is not None and self.vix >= 25:
            flags.append("high-vol")
        if self.vix is not None and self.vix < 14:
            flags.append("complacent")
        if self.yield_curve_2s10s is not None and self.yield_curve_2s10s < 0:
            flags.append("inverted-curve")
        if self.vix_term_structure is not None and self.vix_term_structure < 1.0:
            flags.append("vix-backwardation")
        return flags


def _last_close(ticker: str) -> Optional[float]:
    key = f"close::{ticker}"
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < _TTL_SECONDS:
        return hit[1]  # type: ignore[return-value]
    try:
        df = get_vendor().history(ticker, period="5d", interval="1d")
        val = float(df["Close"].iloc[-1]) if not df.empty else None
    except Exception as exc:
        log.warning("macro fetch failed for %s: %s", ticker, exc)
        val = None
    _cache[key] = (time.time(), val)
    return val


def snapshot() -> MacroSnapshot:
    vix = _last_close(_YF_PROXIES["vix"])
    vix3m = _last_close(_YF_PROXIES["vix3m"])
    y10_raw = _last_close(_YF_PROXIES["us_10y"])
    y2_raw = _last_close(_YF_PROXIES["us_2y"])
    y30_raw = _last_close(_YF_PROXIES["us_30y"])
    # ^TNX/^IRX/^TYX are quoted *10 of yield percent; normalize.
    y10 = y10_raw / 10.0 if y10_raw else None
    y2 = y2_raw / 10.0 if y2_raw else None
    y30 = y30_raw / 10.0 if y30_raw else None
    dxy = _last_close(_YF_PROXIES["dxy"])
    spx = _last_close(_YF_PROXIES["spx"])
    curve = (y10 - y2) * 100 if (y10 is not None and y2 is not None) else None
    term = (vix3m / vix) if (vix and vix3m and vix > 0) else None
    return MacroSnapshot(
        vix=vix,
        vix_3m=vix3m,
        yield_2y=y2,
        yield_10y=y10,
        yield_30y=y30,
        dxy=dxy,
        spx_close=spx,
        yield_curve_2s10s=curve,
        vix_term_structure=term,
    )


def fred_series(series_id: str) -> pd.DataFrame:
    """Fetch a FRED series. Requires FRED_API_KEY for full access; the public
    fredgraph CSV endpoint works without a key but rate-limits aggressively.
    Returns columns ['date', 'value'].
    """
    import requests

    api_key = os.environ.get("FRED_API_KEY")
    if api_key:
        url = (
            "https://api.stlouisfed.org/fred/series/observations"
            f"?series_id={series_id}&api_key={api_key}&file_type=json"
        )
        try:
            data = requests.get(url, timeout=15).json()
            obs = data.get("observations", [])
            return pd.DataFrame(
                [{"date": o["date"], "value": float(o["value"])} for o in obs if o["value"] != "."]
            )
        except Exception as exc:
            log.warning("FRED API fetch failed: %s", exc)
            return pd.DataFrame(columns=["date", "value"])
    # Fallback: public CSV endpoint.
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    try:
        df = pd.read_csv(url)
        df.columns = ["date", "value"]
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        return df.dropna(subset=["value"])
    except Exception as exc:
        log.warning("FRED CSV fetch failed: %s", exc)
        return pd.DataFrame(columns=["date", "value"])
