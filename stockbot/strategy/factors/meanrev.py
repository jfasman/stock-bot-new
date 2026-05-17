from __future__ import annotations

from typing import Dict

import pandas as pd

from ..cross_section import rank_pct


def _short_horizon_return(close: pd.Series, days: int = 5) -> float | None:
    if len(close) < days + 1:
        return None
    try:
        end = float(close.iloc[-1])
        start = float(close.iloc[-(days + 1)])
        if start <= 0:
            return None
        return end / start - 1.0
    except (IndexError, ValueError):
        return None


def meanrev_score(price_history: Dict[str, pd.DataFrame], days: int = 5) -> Dict[str, float]:
    """Short-horizon mean reversion: recent losers expected to outperform.

    Score is the inverse rank of the last N-day return.
    """
    rets: Dict[str, float | None] = {}
    for t, df in price_history.items():
        if df is None or df.empty or "Close" not in df.columns:
            rets[t] = None
            continue
        rets[t] = _short_horizon_return(df["Close"].astype(float), days=days)
    return rank_pct(rets, higher_is_better=False)
