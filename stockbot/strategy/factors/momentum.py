from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd

from ..cross_section import rank_pct


def _ret_12_1(close: pd.Series) -> float | None:
    """12-month return excluding the most recent month (classic Jegadeesh-Titman)."""
    if len(close) < 252:
        return None
    try:
        start = float(close.iloc[-252])
        end = float(close.iloc[-21])
        if start <= 0:
            return None
        return end / start - 1.0
    except (IndexError, ValueError):
        return None


def _risk_adjusted(close: pd.Series) -> float | None:
    if len(close) < 60:
        return None
    rets = close.pct_change().dropna()
    if rets.std() == 0:
        return None
    return float(rets.tail(60).mean() / rets.tail(60).std())


def momentum_score(price_history: Dict[str, pd.DataFrame]) -> Dict[str, float]:
    """Composite of 12-1 month return and risk-adjusted recent return.

    Inputs: ticker -> daily OHLCV DataFrame with 'Close' column.
    """
    r12_1: Dict[str, float | None] = {}
    rar: Dict[str, float | None] = {}
    for t, df in price_history.items():
        if df is None or df.empty or "Close" not in df.columns:
            r12_1[t] = None
            rar[t] = None
            continue
        close = df["Close"].astype(float)
        r12_1[t] = _ret_12_1(close)
        rar[t] = _risk_adjusted(close)
    r1 = rank_pct(r12_1, higher_is_better=True)
    r2 = rank_pct(rar, higher_is_better=True)
    return {t: 0.6 * r1[t] + 0.4 * r2[t] for t in price_history}
