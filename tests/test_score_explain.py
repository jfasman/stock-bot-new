from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from stockbot.config import Config
from stockbot.strategy.score_explain import explain_score


def _synthetic_history(days: int = 200, drift: float = 0.0005, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, 0.015, days)
    close = 100 * np.exp(np.cumsum(rets))
    idx = pd.date_range("2024-01-02", periods=days, freq="B")
    return pd.DataFrame({"Close": close, "Volume": rng.integers(2_000_000, 10_000_000, days)}, index=idx)


@pytest.fixture
def cfg() -> Config:
    return Config(raw={
        "strategy": {
            "rsi_oversold": 30,
            "rsi_overbought": 70,
            "min_avg_volume": 500_000,
            "sentiment_weight": 0.4,
            "min_score_to_trade": 0.55,
            "bear_score_to_trade": -0.55,
        },
        "sentiment": {},
    })


def test_explain_score_returns_breakdown(cfg):
    df = _synthetic_history(seed=42)
    with patch("stockbot.strategy.score_explain.price_data.get_history", return_value=df), \
         patch("stockbot.strategy.score_explain.aggregate", return_value={}):
        b = explain_score(cfg, "TEST", include_factors=False)
    assert b is not None
    assert b.ticker == "TEST"
    # Three technical components with weights that sum to 1.0
    assert len(b.tech_components) == 3
    assert abs(sum(c.weight for c in b.tech_components) - 1.0) < 1e-9
    # Contribution = value * weight for each
    for c in b.tech_components:
        assert abs(c.contribution - c.value * c.weight) < 1e-9


def test_explain_score_classification_thresholds(cfg):
    df = _synthetic_history(drift=0.005, seed=1)  # strong uptrend
    with patch("stockbot.strategy.score_explain.price_data.get_history", return_value=df), \
         patch("stockbot.strategy.score_explain.aggregate", return_value={}):
        b = explain_score(cfg, "BULL", include_factors=False)
    assert b is not None
    # With strong drift, score should be positive and classified LONG or NEUTRAL.
    assert b.classification in ("LONG", "NEUTRAL")
    assert b.thresholds["min_long"] == 0.55
    assert b.thresholds["min_short"] == -0.55


def test_explain_score_returns_none_for_short_history(cfg):
    df = _synthetic_history(days=10)  # < 50, technicals will refuse
    with patch("stockbot.strategy.score_explain.price_data.get_history", return_value=df):
        b = explain_score(cfg, "TINY", include_factors=False)
    assert b is None


def test_blend_math_matches_final_score(cfg):
    df = _synthetic_history(seed=7)
    with patch("stockbot.strategy.score_explain.price_data.get_history", return_value=df), \
         patch("stockbot.strategy.score_explain.aggregate", return_value={}):
        b = explain_score(cfg, "X", include_factors=False)
    # With no sentiment confidence, final score == tech_composite (within clip bounds).
    assert abs(b.final_score - b.tech_composite) < 1e-6


def test_formula_renders(cfg):
    df = _synthetic_history(seed=2)
    with patch("stockbot.strategy.score_explain.price_data.get_history", return_value=df), \
         patch("stockbot.strategy.score_explain.aggregate", return_value={}):
        b = explain_score(cfg, "FMT", include_factors=False)
    text = b.formula()
    # Either branch should mention tech_composite for the CFO to see.
    assert "tech_composite" in text


def test_indicators_populated(cfg):
    df = _synthetic_history(seed=3)
    with patch("stockbot.strategy.score_explain.price_data.get_history", return_value=df), \
         patch("stockbot.strategy.score_explain.aggregate", return_value={}):
        b = explain_score(cfg, "IND", include_factors=False)
    i = b.indicators
    assert 0.0 <= i.bollinger_pct <= 1.0
    assert i.sma_20 > 0 and i.sma_50 > 0
    assert i.avg_volume_20 > 0
    assert i.rsi_label() in ("oversold", "neutral", "overbought")
    assert i.trend_label()
