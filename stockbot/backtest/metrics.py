from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class Metrics:
    total_return: float
    cagr: float
    volatility: float
    sharpe: float
    sortino: float
    max_drawdown: float
    win_rate: Optional[float]
    profit_factor: Optional[float]
    trades: int

    def headline(self) -> str:
        return (
            f"ret {self.total_return*100:+.1f}% | CAGR {self.cagr*100:+.1f}% | "
            f"Sharpe {self.sharpe:.2f} | Sortino {self.sortino:.2f} | "
            f"MaxDD {self.max_drawdown*100:.1f}% | trades {self.trades}"
        )


def compute_metrics(equity_curve: pd.Series, trades: list[dict] | None = None) -> Metrics:
    if equity_curve is None or equity_curve.empty:
        return Metrics(0, 0, 0, 0, 0, 0, None, None, 0)
    eq = equity_curve.astype(float)
    rets = eq.pct_change().dropna()
    total_return = float(eq.iloc[-1] / eq.iloc[0] - 1) if eq.iloc[0] > 0 else 0.0
    n_days = max(1, len(eq))
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (252 / n_days) - 1 if eq.iloc[0] > 0 else 0.0
    vol = float(rets.std() * math.sqrt(252)) if not rets.empty else 0.0
    sharpe = float(rets.mean() / rets.std() * math.sqrt(252)) if rets.std() > 0 else 0.0
    downside = rets[rets < 0]
    sortino = float(rets.mean() / downside.std() * math.sqrt(252)) if not downside.empty and downside.std() > 0 else 0.0
    cum_max = eq.cummax()
    dd = float((eq / cum_max - 1).min())
    win_rate = None
    profit_factor = None
    trade_count = 0
    if trades:
        pnls = [t.get("pnl", 0.0) for t in trades]
        trade_count = len(pnls)
        if pnls:
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p < 0]
            win_rate = len(wins) / len(pnls)
            profit_factor = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else None
    return Metrics(
        total_return=total_return,
        cagr=float(cagr),
        volatility=vol,
        sharpe=sharpe,
        sortino=sortino,
        max_drawdown=dd,
        win_rate=win_rate,
        profit_factor=profit_factor,
        trades=trade_count,
    )


def deflated_sharpe(sr: float, n_trials: int, n_obs: int, skew: float = 0.0, kurt: float = 3.0) -> float:
    """Deflated Sharpe ratio (Bailey & López de Prado, 2014).

    Adjusts an observed Sharpe `sr` for the multiple-testing bias of running
    `n_trials` candidate strategies, plus the non-normality penalty from skew/kurt.
    Returns the probability that the *true* Sharpe is positive.
    """
    if n_trials <= 1 or n_obs <= 1:
        return 0.5
    # Expected max Sharpe under the null (variance-1 SRs).
    from statistics import NormalDist
    nd = NormalDist()
    emc = (1 - 0.5772156649) * nd.inv_cdf(1 - 1.0 / n_trials) + 0.5772156649 * nd.inv_cdf(
        1 - 1.0 / (n_trials * math.e)
    )
    # Variance penalty for finite-sample, skew, kurt.
    sigma_sr = math.sqrt((1 - skew * sr + (kurt - 1) / 4 * sr ** 2) / (n_obs - 1))
    z = (sr - emc * sigma_sr) / sigma_sr if sigma_sr > 0 else 0.0
    return nd.cdf(z)
