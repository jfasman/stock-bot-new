import numpy as np
import pandas as pd

from stockbot.data.fundamentals import Fundamentals
from stockbot.strategy.factors import (
    FactorWeights,
    momentum_score,
    quality_score,
    score_universe,
    value_score,
)


def _synthetic_price_history(tickers, days=300, seed=0):
    rng = np.random.default_rng(seed)
    out = {}
    base_date = pd.Timestamp("2023-01-02")
    for i, t in enumerate(tickers):
        drift = 0.0005 + 0.0002 * i
        vol = 0.012 + 0.002 * (i % 3)
        rets = rng.normal(drift, vol, days)
        prices = 100 * np.exp(np.cumsum(rets))
        idx = pd.date_range(base_date, periods=days, freq="B")
        out[t] = pd.DataFrame({"Close": prices, "Volume": rng.integers(1e6, 1e7, days)}, index=idx)
    return out


def _synthetic_fundamentals(tickers):
    out = {}
    for i, t in enumerate(tickers):
        out[t] = Fundamentals(
            ticker=t,
            sector="tech" if i % 2 == 0 else "energy",
            market_cap=1e9 * (i + 1),
            trailing_pe=10 + i * 5,
            price_to_book=1 + 0.5 * i,
            ev_to_ebitda=8 + i,
            fcf_yield=0.02 + 0.005 * i,
            gross_margin=0.4 - 0.05 * i,
            profit_margin=0.15 - 0.01 * i,
            return_on_equity=0.20 - 0.02 * i,
            debt_to_equity=0.5 + 0.1 * i,
            beta=1.0 + 0.1 * i,
        )
    return out


def test_value_score_low_pe_wins():
    tickers = ["A", "B", "C", "D"]
    f = _synthetic_fundamentals(tickers)
    scores = value_score(f)
    # A has lowest P/E → highest value score
    assert scores["A"] > scores["D"]


def test_momentum_score_runs_without_error():
    tickers = ["A", "B", "C", "D"]
    hist = _synthetic_price_history(tickers)
    scores = momentum_score(hist)
    assert set(scores) == set(tickers)
    assert all(-1.0 <= v <= 1.0 for v in scores.values())


def test_score_universe_returns_breakdown():
    tickers = ["A", "B", "C", "D"]
    f = _synthetic_fundamentals(tickers)
    hist = _synthetic_price_history(tickers)
    report = score_universe(tickers, f, hist, weights=FactorWeights())
    assert set(report.composite) == set(tickers)
    for t in tickers:
        assert set(report.breakdown[t]) == {"value", "momentum", "quality", "lowvol", "size", "meanrev"}


def test_factor_weights_normalized_sum_to_one():
    w = FactorWeights(value=2, momentum=2, quality=2, lowvol=2, size=2, meanrev=2).normalized()
    total = w.value + w.momentum + w.quality + w.lowvol + w.size + w.meanrev
    assert abs(total - 1.0) < 1e-9
