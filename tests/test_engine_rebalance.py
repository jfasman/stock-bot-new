"""Integration test for engine.paper.run_rebalance — proposes + records."""
from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pandas as pd

from stockbot.config import Config
from stockbot.engine.paper import run_rebalance
from stockbot.ops import rebalance as ops_rebalance
from stockbot.portfolio.portfolio import Portfolio


def _cfg(*, enabled: bool = True) -> Config:
    return Config(raw={
        "portfolio": {"starting_cash": 100_000, "max_position_pct": 0.10},
        "risk": {},
        "strategy": {
            "sentiment_weight": 0.4, "factor_weight": 0.0,
            "min_score_to_trade": 0.55, "bear_score_to_trade": -0.55,
            "rsi_oversold": 30, "rsi_overbought": 70, "min_avg_volume": 0,
        },
        "sentiment": {},
        "rebalance": {
            "enabled": enabled,
            "algo": "equal_risk_contribution",
            "max_turnover_per_rebalance": 0.50,
        },
        "watchlist": ["A", "B"],
        "factors": {},
        "leveraged_etfs": {"enabled": False},
    })


def _uptrend_history(days: int = 200) -> pd.DataFrame:
    close = pd.Series(np.linspace(100, 200, days))
    idx = pd.date_range("2024-01-02", periods=days, freq="B")
    return pd.DataFrame(
        {"Open": close, "High": close * 1.01, "Low": close * 0.99,
         "Close": close, "Volume": [1_000_000] * days},
        index=idx,
    )


def test_run_rebalance_skips_when_disabled():
    cfg = _cfg(enabled=False)
    portfolio = Portfolio(starting_cash=100_000)
    assert run_rebalance(cfg, portfolio, ["A", "B"]) is None


def test_run_rebalance_records_proposal_with_picks():
    """With existing positions in the book, run_rebalance should compute
    weights and record a pending proposal — independent of whether
    generate_ideas surfaces new candidates."""
    from datetime import datetime

    from stockbot.data.fundamentals import Fundamentals
    from stockbot.strategy.ideas import Idea

    cfg = _cfg(enabled=True)
    portfolio = Portfolio(starting_cash=100_000)
    # Seed a real position so the rebalancer has a book to operate on.
    portfolio.open_position(
        ticker="A", instrument="equity", direction="long",
        quantity=100, entry_price=50.0,
    )

    history = {"A": _uptrend_history(), "B": _uptrend_history()}

    def get_history(t, *args, **kwargs):
        return history.get(t.upper(), pd.DataFrame())

    seeded_idea = Idea(
        ticker="B", direction="long", instrument="equity", score=0.7,
        last_price=150.0, rsi=55.0, reasons=["seeded"],
        sentiment_net=0.0, sentiment_confidence=0.0,
    )

    with patch("stockbot.engine.paper.price_data.get_history", side_effect=get_history), \
         patch("stockbot.engine.paper.generate_ideas", return_value=[seeded_idea]), \
         patch("stockbot.data.fundamentals.get_fundamentals",
               return_value=Fundamentals(ticker="X", sector="Tech", beta=1.0)):
        pid = run_rebalance(cfg, portfolio, ["A", "B"])

    assert pid is not None
    row = ops_rebalance.get(pid)
    assert row is not None
    assert row["status"] == "pending"
    assert row["algo"] == "equal_risk_contribution"
    # Book had A; new pick was B → proposal touches both.
    tickers_in_changes = {c["ticker"] for c in row["changes"]}
    assert "A" in tickers_in_changes
    assert "B" in tickers_in_changes


def test_run_rebalance_returns_none_when_nothing_to_rebalance():
    """Empty book + no qualifying picks → nothing to propose."""
    cfg = _cfg(enabled=True)
    portfolio = Portfolio(starting_cash=100_000)

    # No price history → ideas will return [] → nothing to rebalance.
    def empty_history(t, *args, **kwargs):
        return pd.DataFrame()

    with patch("stockbot.engine.paper.price_data.get_history", side_effect=empty_history), \
         patch("stockbot.strategy.ideas.price_data.get_history", side_effect=empty_history), \
         patch("stockbot.strategy.ideas.aggregate", return_value={}):
        pid = run_rebalance(cfg, portfolio, ["A"])

    assert pid is None
