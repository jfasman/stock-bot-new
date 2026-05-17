import numpy as np
import pandas as pd

from stockbot.portfolio.var import historical_var, parametric_var


def _normal_returns(n=1000, mu=0.0005, sigma=0.012, seed=0):
    rng = np.random.default_rng(seed)
    return pd.Series(rng.normal(mu, sigma, n))


def test_parametric_var_nonnegative():
    rets = _normal_returns()
    out = parametric_var(rets, portfolio_value=100_000)
    assert out.var_95 >= 0
    assert out.var_99 >= out.var_95
    assert out.cvar_95 >= out.var_95


def test_historical_var_within_reasonable_band():
    rets = _normal_returns()
    out = historical_var(rets, portfolio_value=100_000)
    # For 100k portfolio and σ ~ 1.2% daily, 95% VaR ought to be a few thousand bucks.
    assert 1_000 < out.var_95 < 10_000


def test_empty_returns_zero():
    out = parametric_var(pd.Series(dtype=float), 100_000)
    assert out.var_95 == 0
