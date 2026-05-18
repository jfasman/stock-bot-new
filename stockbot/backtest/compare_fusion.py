"""Side-by-side: unfused (legacy two-way blend) vs fused (three-way) backtest.

Roadmap §13.6 acceptance criterion: "the walk-forward report adds a side-by-side:
unfused (legacy) vs. fused (new) on the same window." This module is the
implementation — given a universe and a window, it runs the engine twice with
identical PIT data and identical scoring code-path, differing only in whether
the factor composite is folded into the per-ticker score.

The unfused leg is the historical behavior: tech composite drives the rank,
sentiment is omitted (PIT sentiment is not available in backtest). The fused
leg additionally consumes a per-asof cross-sectional factor composite computed
on the same PIT slice.

Caveat: fundamentals are NOT point-in-time today (vendor returns latest).
That's a known §11 gap and applies equally to both legs of the comparison,
so the *delta* between them is still informative. The absolute numbers
are not — fundamentals look-ahead inflates both equally.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Dict, Optional

import pandas as pd

from ..config import Config
from ..data.fundamentals import Fundamentals
from ..strategy.factors.fusion import compute_factor_composites
from ..strategy.scorer import composite_score, read_technicals
from .engine import Backtester, BacktestResult
from .metrics import Metrics

log = logging.getLogger(__name__)


@dataclass
class FusionComparison:
    start: str
    end: str
    unfused: Metrics
    fused: Metrics
    unfused_trades: int
    fused_trades: int

    def to_row(self) -> Dict[str, float]:
        """Flat dict for tabular reporting."""
        return {
            "window": f"{self.start} → {self.end}",
            "unfused_cagr": self.unfused.cagr,
            "fused_cagr": self.fused.cagr,
            "delta_cagr": self.fused.cagr - self.unfused.cagr,
            "unfused_sharpe": self.unfused.sharpe,
            "fused_sharpe": self.fused.sharpe,
            "delta_sharpe": self.fused.sharpe - self.unfused.sharpe,
            "unfused_max_dd": self.unfused.max_drawdown,
            "fused_max_dd": self.fused.max_drawdown,
            "unfused_trades": self.unfused_trades,
            "fused_trades": self.fused_trades,
        }


def _score_one(
    cfg: Config,
    ticker: str,
    sliced_df: pd.DataFrame,
    factor_composite: Optional[float],
) -> Optional[float]:
    """Run the same scoring math the live system uses (no sentiment in backtest)."""
    tech = read_technicals(sliced_df, cfg)
    if tech is None:
        return None
    score, _ = composite_score(cfg, tech, None, factor_composite=factor_composite)
    return score


def _signal_factory(
    cfg: Config,
    tickers: list[str],
    fundamentals: Dict[str, Fundamentals],
    *,
    use_factor: bool,
    top_quintile: float = 0.20,
):
    """Build a SignalFn for the backtester. `use_factor=True` is the fused leg."""

    def signal_fn(asof: date, sliced_history: Dict[str, pd.DataFrame]) -> Dict[str, float]:
        factor_map: Dict[str, float] = {}
        if use_factor:
            factor_map = compute_factor_composites(
                cfg,
                tickers,
                fundamentals_loader=lambda t: fundamentals.get(t, Fundamentals(ticker=t)),
                price_loader=lambda t: sliced_history.get(t, pd.DataFrame()),
            )
        scored: list[tuple[str, float]] = []
        for t in tickers:
            df = sliced_history.get(t)
            if df is None or df.empty:
                continue
            s = _score_one(cfg, t, df, factor_map.get(t))
            if s is None:
                continue
            scored.append((t, s))
        if not scored:
            return {}
        scored.sort(key=lambda kv: kv[1], reverse=True)
        n_long = max(1, int(round(len(scored) * top_quintile)))
        positive = [(t, s) for t, s in scored[:n_long] if s > 0]
        if not positive:
            return {}
        w = 1.0 / len(positive)
        return {t: w for t, _ in positive}

    return signal_fn


def compare_fusion(
    cfg: Config,
    price_history: Dict[str, pd.DataFrame],
    fundamentals: Dict[str, Fundamentals],
    start: str,
    end: str,
    *,
    starting_cash: float = 100_000.0,
    rebalance_freq: int = 5,
    top_quintile: float = 0.20,
) -> FusionComparison:
    """Run the same backtest window twice and return both legs' metrics."""
    tickers = sorted(price_history.keys())

    bt_unfused = Backtester(
        price_history, starting_cash=starting_cash, rebalance_freq=rebalance_freq
    )
    bt_fused = Backtester(
        price_history, starting_cash=starting_cash, rebalance_freq=rebalance_freq
    )

    unfused_signal = _signal_factory(
        cfg, tickers, fundamentals, use_factor=False, top_quintile=top_quintile
    )
    fused_signal = _signal_factory(
        cfg, tickers, fundamentals, use_factor=True, top_quintile=top_quintile
    )

    unfused_result: BacktestResult = bt_unfused.run(unfused_signal, start, end)
    fused_result: BacktestResult = bt_fused.run(fused_signal, start, end)

    return FusionComparison(
        start=start,
        end=end,
        unfused=unfused_result.metrics,
        fused=fused_result.metrics,
        unfused_trades=len(unfused_result.trades),
        fused_trades=len(fused_result.trades),
    )
