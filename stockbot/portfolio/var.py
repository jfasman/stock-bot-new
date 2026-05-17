from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class VaRResult:
    var_95: float
    var_99: float
    cvar_95: float          # expected shortfall (conditional VaR)
    cvar_99: float
    method: str             # 'parametric' | 'historical'
    horizon_days: int
    notes: str = ""


def _z(p: float) -> float:
    """Inverse-normal CDF for common confidence levels. Approximation via Acklam."""
    # Beasley-Springer-Moro is overkill; use a small table for the levels we need.
    table = {0.95: 1.6448536269514722, 0.99: 2.3263478740408408}
    if p in table:
        return table[p]
    # Crude fallback.
    from statistics import NormalDist
    return NormalDist().inv_cdf(p)


def parametric_var(returns: pd.Series, portfolio_value: float, horizon_days: int = 1) -> VaRResult:
    """Variance-covariance VaR. Assumes returns are normal, which is wrong, but
    cheap to compute and useful as a baseline. Compare against historical_var."""
    if returns is None or returns.empty:
        return VaRResult(0, 0, 0, 0, "parametric", horizon_days, "no return data")
    mu = float(returns.mean())
    sigma = float(returns.std(ddof=0))
    scale = math.sqrt(horizon_days)
    sig_h = sigma * scale
    mu_h = mu * horizon_days
    var95 = portfolio_value * max(0.0, (-mu_h + _z(0.95) * sig_h))
    var99 = portfolio_value * max(0.0, (-mu_h + _z(0.99) * sig_h))
    # ES under normal: σ * φ(z) / (1 - α) - μ
    from math import exp, pi
    phi95 = (1 / math.sqrt(2 * pi)) * exp(-_z(0.95) ** 2 / 2)
    phi99 = (1 / math.sqrt(2 * pi)) * exp(-_z(0.99) ** 2 / 2)
    es95 = portfolio_value * max(0.0, (-mu_h + sig_h * phi95 / 0.05))
    es99 = portfolio_value * max(0.0, (-mu_h + sig_h * phi99 / 0.01))
    return VaRResult(var95, var99, es95, es99, "parametric", horizon_days)


def historical_var(returns: pd.Series, portfolio_value: float, horizon_days: int = 1) -> VaRResult:
    if returns is None or returns.empty:
        return VaRResult(0, 0, 0, 0, "historical", horizon_days, "no return data")
    if horizon_days > 1:
        # Aggregate non-overlapping multi-day returns for accuracy.
        chunks = [returns.iloc[i:i + horizon_days].sum() for i in range(0, len(returns), horizon_days)]
        agg = pd.Series(chunks).dropna()
    else:
        agg = returns.dropna()
    if agg.empty:
        return VaRResult(0, 0, 0, 0, "historical", horizon_days, "insufficient data")
    losses = -agg
    var95 = portfolio_value * float(np.percentile(losses, 95))
    var99 = portfolio_value * float(np.percentile(losses, 99))
    tail95 = losses[losses >= np.percentile(losses, 95)]
    tail99 = losses[losses >= np.percentile(losses, 99)]
    es95 = portfolio_value * float(tail95.mean()) if not tail95.empty else var95
    es99 = portfolio_value * float(tail99.mean()) if not tail99.empty else var99
    return VaRResult(max(0, var95), max(0, var99), max(0, es95), max(0, es99), "historical", horizon_days)


def portfolio_returns(positions: list[dict], price_history: dict[str, pd.DataFrame]) -> pd.Series:
    """Build a portfolio return series from current positions and history.

    `positions`: list of dicts with keys {ticker, weight}. Weights should sum to ~1
    (cash is the remainder). History is daily Close prices.
    """
    if not positions:
        return pd.Series(dtype=float)
    rets_frame = []
    weights = []
    for p in positions:
        df = price_history.get(p["ticker"].upper())
        if df is None or df.empty:
            continue
        r = df["Close"].astype(float).pct_change().dropna()
        rets_frame.append(r.rename(p["ticker"].upper()))
        weights.append(p["weight"])
    if not rets_frame:
        return pd.Series(dtype=float)
    df = pd.concat(rets_frame, axis=1).dropna(how="all")
    w = np.array(weights)
    w = w / w.sum() if w.sum() > 0 else w
    aligned = df.fillna(0.0).values
    return pd.Series(aligned @ w, index=df.index)
