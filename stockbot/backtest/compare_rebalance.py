"""Side-by-side: per-position sizing only vs per-position + book rebalance.

Roadmap §13.8 acceptance criterion: "The walk-forward report compares (a)
per-position sizing only (current) vs. (b) per-position + book rebalance
(new). The comparison is the empirical answer to 'did book-level sizing
help?' — same contract as the gated-vs-ungated comparison in §13.4."

Implementation mirrors `compare_fusion.py`: same engine, same window, same
signal_fn — the only difference is whether the Backtester's
``book_rebalancer`` hook is wired up.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Optional

import pandas as pd

from ..portfolio.rebalancer import RebalanceConfig, make_backtest_hook
from .engine import Backtester, BacktestResult, SignalFn
from .metrics import Metrics

log = logging.getLogger(__name__)


@dataclass
class RebalanceComparison:
    start: str
    end: str
    unrebalanced: Metrics
    rebalanced: Metrics
    unrebalanced_trades: int
    rebalanced_trades: int
    rebalancer_algo: str

    def to_row(self) -> Dict[str, float]:
        """Flat dict for tabular reporting."""
        return {
            "window": f"{self.start} → {self.end}",
            "algo": self.rebalancer_algo,
            "unbal_cagr": self.unrebalanced.cagr,
            "rebal_cagr": self.rebalanced.cagr,
            "delta_cagr": self.rebalanced.cagr - self.unrebalanced.cagr,
            "unbal_sharpe": self.unrebalanced.sharpe,
            "rebal_sharpe": self.rebalanced.sharpe,
            "delta_sharpe": self.rebalanced.sharpe - self.unrebalanced.sharpe,
            "unbal_max_dd": self.unrebalanced.max_drawdown,
            "rebal_max_dd": self.rebalanced.max_drawdown,
            "unbal_trades": self.unrebalanced_trades,
            "rebal_trades": self.rebalanced_trades,
        }


def compare_rebalance(
    price_history: Dict[str, pd.DataFrame],
    signal_fn: SignalFn,
    start: str,
    end: str,
    *,
    rebalance_config: Optional[RebalanceConfig] = None,
    starting_cash: float = 100_000.0,
    rebalance_freq: int = 5,
    vol_loader: Optional[callable] = None,
) -> RebalanceComparison:
    """Run the same backtest window twice and return both legs' metrics.

    Both legs use identical signal_fn + price history. The rebalanced leg
    additionally pipes (current_weights, signal_weights) through the
    book-level rebalancer between signal_fn and the engine's order layer.

    `signal_fn` is the caller's responsibility — typical caller is the
    `backtest-fusion` flow or a hand-rolled factor signal. We don't bake
    one in here because rebalance and fusion are orthogonal.
    """
    rebalance_config = rebalance_config or RebalanceConfig()

    bt_unbal = Backtester(
        price_history, starting_cash=starting_cash, rebalance_freq=rebalance_freq,
    )
    hook = make_backtest_hook(rebalance_config, vol_loader=vol_loader)
    bt_rebal = Backtester(
        price_history, starting_cash=starting_cash, rebalance_freq=rebalance_freq,
        book_rebalancer=hook,
    )

    unbal_result: BacktestResult = bt_unbal.run(signal_fn, start, end)
    rebal_result: BacktestResult = bt_rebal.run(signal_fn, start, end)

    return RebalanceComparison(
        start=start, end=end,
        unrebalanced=unbal_result.metrics, rebalanced=rebal_result.metrics,
        unrebalanced_trades=len(unbal_result.trades),
        rebalanced_trades=len(rebal_result.trades),
        rebalancer_algo=rebalance_config.algo,
    )
