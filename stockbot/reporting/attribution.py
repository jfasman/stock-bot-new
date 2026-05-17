from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

import numpy as np
import pandas as pd


@dataclass
class FactorAttribution:
    factor_returns: Dict[str, float]    # factor -> contribution to portfolio return
    idiosyncratic: float
    r_squared: float


def factor_attribution(
    portfolio_returns: pd.Series,
    factor_returns: pd.DataFrame,
) -> FactorAttribution:
    """OLS regression of portfolio returns on a set of factor returns.

    `factor_returns` columns are factor names; rows aligned with `portfolio_returns`.
    Coefficients ARE the contribution per unit factor return; we report the
    cumulative contribution over the sample window.
    """
    df = pd.concat([portfolio_returns.rename("y"), factor_returns], axis=1).dropna()
    if df.empty or df.shape[1] < 2:
        return FactorAttribution(factor_returns={}, idiosyncratic=0.0, r_squared=0.0)
    y = df["y"].values
    X = df.drop(columns=["y"]).values
    X1 = np.column_stack([np.ones(len(X)), X])
    try:
        beta, *_ = np.linalg.lstsq(X1, y, rcond=None)
    except np.linalg.LinAlgError:
        return FactorAttribution(factor_returns={}, idiosyncratic=float(y.sum()), r_squared=0.0)
    y_hat = X1 @ beta
    resid = y - y_hat
    ss_res = float((resid ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0

    contrib: Dict[str, float] = {}
    for i, factor in enumerate(df.columns[1:]):
        # Approximate cumulative contribution = beta * sum(factor_return)
        contrib[factor] = float(beta[i + 1] * df[factor].sum())
    return FactorAttribution(
        factor_returns=contrib,
        idiosyncratic=float(resid.sum()),
        r_squared=r2,
    )


def pnl_decomposition(trades: list[dict]) -> Dict[str, float]:
    """Group realized PnL by ticker / instrument / direction for transparency."""
    by_ticker: Dict[str, float] = {}
    by_instrument: Dict[str, float] = {}
    by_direction: Dict[str, float] = {}
    for t in trades:
        pnl = float(t.get("pnl", 0.0) or 0.0)
        by_ticker[t.get("ticker", "?")] = by_ticker.get(t.get("ticker", "?"), 0.0) + pnl
        by_instrument[t.get("instrument", "?")] = by_instrument.get(t.get("instrument", "?"), 0.0) + pnl
        by_direction[t.get("direction", "?")] = by_direction.get(t.get("direction", "?"), 0.0) + pnl
    return {"by_ticker": by_ticker, "by_instrument": by_instrument, "by_direction": by_direction}
