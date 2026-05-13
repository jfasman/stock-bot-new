import numpy as np
import pandas as pd

from stockbot.config import Config
from stockbot.sentiment.aggregator import AggregateSentiment
from stockbot.strategy.scorer import composite_score, read_technicals


def _cfg():
    return Config(raw={
        "strategy": {
            "rsi_oversold": 30, "rsi_overbought": 70,
            "min_avg_volume": 0, "sentiment_weight": 0.5,
        }
    })


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
