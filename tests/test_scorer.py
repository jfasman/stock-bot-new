import numpy as np
import pandas as pd
import pytest

from stockbot.config import Config
from stockbot.sentiment.aggregator import AggregateSentiment
from stockbot.strategy.scorer import composite_score, read_technicals


def _cfg(**overrides):
    raw = {
        "strategy": {
            "rsi_oversold": 30, "rsi_overbought": 70,
            "min_avg_volume": 0, "sentiment_weight": 0.5,
        }
    }
    raw["strategy"].update(overrides)
    return Config(raw=raw)


def _uptrend_df(n=120):
    close = pd.Series(np.linspace(100, 200, n))
    df = pd.DataFrame({
        "Open": close, "High": close * 1.01, "Low": close * 0.99,
        "Close": close, "Volume": pd.Series([1_000_000] * n),
    })
    return df


def _downtrend_df(n=120):
    close = pd.Series(np.linspace(200, 100, n))
    df = pd.DataFrame({
        "Open": close, "High": close * 1.01, "Low": close * 0.99,
        "Close": close, "Volume": pd.Series([1_000_000] * n),
    })
    return df


def test_uptrend_yields_positive_technical():
    tech = read_technicals(_uptrend_df(), _cfg())
    assert tech is not None
    assert tech.composite > 0


def test_downtrend_yields_negative_technical():
    tech = read_technicals(_downtrend_df(), _cfg())
    assert tech is not None
    assert tech.composite < 0


def test_short_history_returns_none():
    assert read_technicals(_uptrend_df(20), _cfg()) is None


def test_sentiment_blend_shifts_score():
    tech = read_technicals(_uptrend_df(), _cfg())
    pos_sent = AggregateSentiment("X", net=0.9, confidence=0.9, sources={}, sample_texts=[])
    neg_sent = AggregateSentiment("X", net=-0.9, confidence=0.9, sources={}, sample_texts=[])
    pos_score, _ = composite_score(_cfg(), tech, pos_sent)
    neg_score, _ = composite_score(_cfg(), tech, neg_sent)
    assert pos_score > neg_score


# -- roadmap §13.6 factor fusion --------------------------------------------

def test_no_factor_no_sentiment_score_equals_tech():
    """Both non-tech inputs missing → tech absorbs all weight."""
    tech = read_technicals(_uptrend_df(), _cfg())
    score, _ = composite_score(_cfg(), tech, None)
    assert score == pytest.approx(np.clip(tech.composite, -1.0, 1.0))


def test_factor_present_sentiment_missing_uses_two_way_blend():
    """When sentiment is absent, the blend is (1 - factor_w) × tech + factor_w × factor."""
    cfg = _cfg(sentiment_weight=0.4, factor_weight=0.3)
    tech = read_technicals(_uptrend_df(), cfg)
    score, _ = composite_score(cfg, tech, None, factor_composite=0.5)
    expected = np.clip(0.7 * tech.composite + 0.3 * 0.5, -1.0, 1.0)
    assert score == pytest.approx(expected)


def test_factor_missing_preserves_legacy_two_way_blend():
    """Backward compat: no factor → behave identically to pre-fusion blend."""
    cfg = _cfg(sentiment_weight=0.4, factor_weight=0.3)
    tech = read_technicals(_uptrend_df(), cfg)
    sent = AggregateSentiment("X", net=0.8, confidence=0.5, sources={}, sample_texts=[])
    score, _ = composite_score(cfg, tech, sent)  # factor_composite defaults to None
    expected = np.clip(0.6 * tech.composite + 0.4 * (0.8 * 0.5), -1.0, 1.0)
    assert score == pytest.approx(expected)


def test_three_way_blend_uses_all_three_weights():
    """All three inputs present → tech_w = 1 - sent_w - factor_w; each term contributes."""
    cfg = _cfg(sentiment_weight=0.4, factor_weight=0.3)
    tech = read_technicals(_uptrend_df(), cfg)
    sent = AggregateSentiment("X", net=0.8, confidence=0.5, sources={}, sample_texts=[])
    score, _ = composite_score(cfg, tech, sent, factor_composite=0.6)
    expected = np.clip(
        0.3 * tech.composite + 0.4 * (0.8 * 0.5) + 0.3 * 0.6,
        -1.0, 1.0,
    )
    assert score == pytest.approx(expected)


def test_factor_contribution_surfaces_in_reasons():
    cfg = _cfg(factor_weight=0.3)
    tech = read_technicals(_uptrend_df(), cfg)
    _, reasons = composite_score(cfg, tech, None, factor_composite=0.42)
    assert any("factor" in r and "0.42" in r for r in reasons)


def test_misconfigured_weights_raise():
    """sentiment_weight + factor_weight > 1 leaves negative room for tech → fail loud."""
    cfg = _cfg(sentiment_weight=0.7, factor_weight=0.5)
    tech = read_technicals(_uptrend_df(), cfg)
    with pytest.raises(ValueError, match="exceeds 1.0"):
        composite_score(cfg, tech, None, factor_composite=0.1)
