import numpy as np
import pandas as pd

from stockbot.strategy import indicators as ind


def _ramp(n=120):
    return pd.Series(np.linspace(100, 200, n))


def test_sma_matches_rolling_mean():
    s = _ramp()
    expected = s.rolling(20).mean()
    pd.testing.assert_series_equal(ind.sma(s, 20), expected, check_names=False)


def test_rsi_in_range():
    s = _ramp() + np.random.RandomState(0).normal(0, 1, 120)
    r = ind.rsi(s, 14).dropna()
    assert ((r >= 0) & (r <= 100)).all()


def test_macd_components_length():
    s = _ramp()
    macd, sig, hist = ind.macd(s)
    assert len(macd) == len(s) == len(sig) == len(hist)


def test_atr_positive():
    n = 60
    high = pd.Series(np.linspace(110, 130, n))
    low = pd.Series(np.linspace(100, 120, n))
    close = (high + low) / 2
    df = pd.DataFrame({"High": high, "Low": low, "Close": close})
    a = ind.atr(df).dropna()
    assert (a > 0).all()
