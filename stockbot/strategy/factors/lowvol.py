from __future__ import annotations

from typing import Dict

import pandas as pd

from ..cross_section import rank_pct


def _realized_vol(close: pd.Series, window: int = 60) -> float | None:
    if len(close) < window + 1:
        return None
    rets = close.pct_change().dropna().tail(window)
    if rets.empty:
        return None
    return float(rets.std() * (252 ** 0.5))


def lowvol_score(price_history: Dict[str, pd.DataFrame]) -> Dict[str, float]:
    """Low-volatility factor: lower realized vol scores higher."""
    vols: Dict[str, float | None] = {}
    for t, df in price_history.items():
        if df is None or df.empty or "Close" not in df.columns:
            vols[t] = None
            continue
        vols[t] = _realized_vol(df["Close"].astype(float))
    return rank_pct(vols, higher_is_better=False)
