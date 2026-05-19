"""Tests for backtest/compare_rebalance.py — unrebalanced vs rebalanced side-by-side."""
from __future__ import annotations

import numpy as np
import pandas as pd

from stockbot.backtest.compare_rebalance import RebalanceComparison, compare_rebalance
from stockbot.portfolio.rebalancer import RebalanceConfig


def _synthetic(tickers: list[str], *, days: int = 300, seed: int = 1) -> dict:
    """OHLCV series with mildly different drifts."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2022-01-03", periods=days, freq="B")
    out: dict = {}
    for i, t in enumerate(tickers):
        drift = 0.0005 + 0.0001 * i
        rets = rng.normal(drift, 0.012, days)
        close = 100 * np.exp(np.cumsum(rets))
        out[t] = pd.DataFrame(
            {"Open": close, "High": close * 1.005, "Low": close * 0.995,
             "Close": close, "Volume": rng.integers(2_000_000, 8_000_000, days)},
            index=idx,
        )
    return out


def _equal_weight_signal(tickers: list[str], cap: float = 0.10):
    def signal(asof, sliced):
        return {t: cap for t in tickers}
    return signal


def test_compare_rebalance_returns_paired_metrics():
    tickers = [f"T{i}" for i in range(6)]
    history = _synthetic(tickers, days=300, seed=3)
    cmp = compare_rebalance(
        history,
        _equal_weight_signal(tickers),
        start="2022-03-01", end="2022-11-30",
        rebalance_config=RebalanceConfig(max_turnover_per_rebalance=1.0),
        rebalance_freq=10,
    )
    assert isinstance(cmp, RebalanceComparison)
    assert cmp.start == "2022-03-01"
    assert cmp.end == "2022-11-30"
    # Both legs ran end-to-end on the same window.
    for m in (cmp.unrebalanced, cmp.rebalanced):
        assert isinstance(m.sharpe, float)
        assert isinstance(m.cagr, float)
        assert isinstance(m.max_drawdown, float)


def test_rebalanced_leg_actually_changes_allocations():
    """With vol_loader feeding different vols per ticker, the rebalanced leg
    must produce a different weighting than the unrebalanced (equal-weight) leg."""
    tickers = [f"T{i}" for i in range(6)]
    history = _synthetic(tickers, days=300, seed=11)
    vols = {f"T{i}": 0.10 + i * 0.05 for i in range(6)}  # spread vols
    cmp = compare_rebalance(
        history,
        _equal_weight_signal(tickers),
        start="2022-03-01", end="2022-11-30",
        rebalance_config=RebalanceConfig(max_turnover_per_rebalance=1.0),
        rebalance_freq=10,
        vol_loader=lambda t: vols.get(t),
    )
    # Either the trade count differs or the final return does.
    assert (
        cmp.unrebalanced_trades != cmp.rebalanced_trades
        or abs(cmp.rebalanced.cagr - cmp.unrebalanced.cagr) > 1e-9
    )


def test_to_row_emits_flat_dict():
    tickers = [f"T{i}" for i in range(6)]
    history = _synthetic(tickers, days=200, seed=7)
    cmp = compare_rebalance(
        history, _equal_weight_signal(tickers),
        start="2022-03-01", end="2022-10-30",
        rebalance_config=RebalanceConfig(max_turnover_per_rebalance=1.0),
        rebalance_freq=10,
    )
    row = cmp.to_row()
    assert "delta_cagr" in row
    assert "delta_sharpe" in row
    assert row["algo"] == "equal_risk_contribution"
    assert row["unbal_trades"] == cmp.unrebalanced_trades
    assert row["rebal_trades"] == cmp.rebalanced_trades


def test_uses_supplied_algo_in_config():
    """vol_target_book vs ERC should be reflected in the algo field."""
    tickers = [f"T{i}" for i in range(4)]
    history = _synthetic(tickers, days=200)
    cmp = compare_rebalance(
        history, _equal_weight_signal(tickers),
        start="2022-03-01", end="2022-10-30",
        rebalance_config=RebalanceConfig(
            algo="vol_target_book", target_vol=0.10,
            max_turnover_per_rebalance=1.0,
        ),
        rebalance_freq=10,
    )
    assert cmp.rebalancer_algo == "vol_target_book"
