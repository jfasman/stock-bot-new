"""Tests for backtest/compare_fusion.py — unfused vs fused side-by-side.

Smoke-level: both legs run cleanly on synthetic data, produce comparable
metrics, and the fused leg actually consults the factor map when factor_weight
is non-zero.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from stockbot.config import Config
from stockbot.data.fundamentals import Fundamentals
from stockbot.backtest.compare_fusion import FusionComparison, compare_fusion


def _synthetic(tickers: list[str], *, days: int = 400, seed: int = 1) -> dict:
    """A handful of OHLCV series with mildly differentiated drifts so the
    rank-based signal has something to bite on."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2022-01-03", periods=days, freq="B")
    out: dict = {}
    for i, t in enumerate(tickers):
        drift = 0.0005 + 0.0001 * i  # gentle spread across names
        rets = rng.normal(drift, 0.012, days)
        close = 100 * np.exp(np.cumsum(rets))
        out[t] = pd.DataFrame(
            {
                "Open": close,
                "High": close * 1.005,
                "Low": close * 0.995,
                "Close": close,
                "Volume": rng.integers(2_000_000, 8_000_000, days),
            },
            index=idx,
        )
    return out


def _cfg(*, factor_weight: float = 0.3, peer_pool: list[str] | None = None) -> Config:
    return Config(raw={
        "strategy": {
            "rsi_oversold": 30, "rsi_overbought": 70,
            "min_avg_volume": 0, "sentiment_weight": 0.4,
            "factor_weight": factor_weight,
        },
        "factors": {
            "min_universe": 5,
            "peer_pool": peer_pool or [],
            "sector_neutral": False,
        },
        "watchlist": [],
    })


def _funds(tickers: list[str]) -> dict[str, Fundamentals]:
    # All in same sector so sector_neutral False doesn't matter; provide enough
    # fundamentals fields that the factor functions have signal.
    return {
        t: Fundamentals(
            ticker=t, sector="Technology",
            market_cap=1e10 + i * 1e9,
            trailing_pe=15 + i, forward_pe=14 + i,
            return_on_equity=0.10 + 0.01 * i,
            return_on_assets=0.05 + 0.005 * i,
            gross_margin=0.40, operating_margin=0.20, profit_margin=0.15,
            beta=1.0,
        )
        for i, t in enumerate(tickers)
    }


def test_compare_fusion_returns_paired_metrics():
    tickers = [f"T{i}" for i in range(8)]
    history = _synthetic(tickers, days=400, seed=3)
    funds = _funds(tickers)
    cfg = _cfg(factor_weight=0.3)

    cmp = compare_fusion(
        cfg, history, funds,
        start="2022-03-01", end="2023-06-30",
        rebalance_freq=10, top_quintile=0.30,
    )
    assert isinstance(cmp, FusionComparison)
    # Both legs ran end-to-end on the same window.
    assert cmp.start == "2022-03-01"
    assert cmp.end == "2023-06-30"
    # Sanity: metrics object is populated (sharpe / cagr / max_dd are floats).
    for m in (cmp.unfused, cmp.fused):
        assert isinstance(m.sharpe, float)
        assert isinstance(m.cagr, float)
        assert isinstance(m.max_drawdown, float)


def test_fused_and_unfused_actually_diverge():
    """With factor_weight=0.5 and gentle drift differentiation, the two legs
    must produce *some* different selection at least once during the window —
    otherwise fusion is a no-op and the side-by-side is meaningless."""
    tickers = [f"T{i}" for i in range(8)]
    history = _synthetic(tickers, days=400, seed=11)
    funds = _funds(tickers)
    cfg = _cfg(factor_weight=0.5)

    cmp = compare_fusion(
        cfg, history, funds,
        start="2022-03-01", end="2023-06-30",
        rebalance_freq=10, top_quintile=0.25,
    )
    # Trade counts may differ slightly; equity curves differ if any rebalance
    # picked different tickers. Either condition proves the legs diverged.
    assert (
        cmp.unfused_trades != cmp.fused_trades
        or abs(cmp.fused.cagr - cmp.unfused.cagr) > 1e-9
    )


def test_compare_with_factor_weight_zero_matches_unfused_logic():
    """factor_weight=0 makes the fused leg's factor map empty → behaves like
    unfused. Trade counts should be identical (or nearly — synthetic noise
    makes exact equality fragile, but ballpark must match)."""
    tickers = [f"T{i}" for i in range(8)]
    history = _synthetic(tickers, days=300, seed=5)
    funds = _funds(tickers)
    cfg = _cfg(factor_weight=0.0)

    cmp = compare_fusion(
        cfg, history, funds,
        start="2022-03-01", end="2023-01-30",
        rebalance_freq=10, top_quintile=0.30,
    )
    assert cmp.unfused_trades == cmp.fused_trades


def test_to_row_emits_flat_dict():
    tickers = [f"T{i}" for i in range(6)]
    history = _synthetic(tickers, days=250)
    funds = _funds(tickers)
    cfg = _cfg(factor_weight=0.3)
    cmp = compare_fusion(cfg, history, funds, start="2022-03-01", end="2022-11-30", rebalance_freq=10)
    row = cmp.to_row()
    assert "delta_cagr" in row
    assert "delta_sharpe" in row
    assert row["unfused_trades"] == cmp.unfused_trades
    assert row["fused_trades"] == cmp.fused_trades
