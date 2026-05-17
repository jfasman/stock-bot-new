import math

from stockbot.data.greeks import bs_price, greeks, implied_vol


def test_atm_call_price_positive():
    p = bs_price(spot=100, strike=100, t_years=0.25, rate=0.04, sigma=0.20, option_type="call")
    assert p > 0
    assert p < 10  # ATM call on a $100 stock at 20% vol for 3 months should be modest


def test_put_call_parity():
    spot, strike, t, r, sigma = 100, 100, 0.5, 0.04, 0.25
    call = bs_price(spot, strike, t, r, sigma, "call")
    put = bs_price(spot, strike, t, r, sigma, "put")
    # call - put = spot - strike * exp(-r*t)
    parity = spot - strike * math.exp(-r * t)
    assert abs((call - put) - parity) < 1e-4


def test_call_delta_in_range():
    g = greeks(spot=100, strike=100, t_years=0.5, rate=0.04, sigma=0.25, option_type="call")
    assert 0 < g.delta < 1
    assert g.gamma > 0
    assert g.vega > 0


def test_implied_vol_roundtrip():
    spot, strike, t, r, sigma = 100, 105, 0.3, 0.04, 0.28
    price = bs_price(spot, strike, t, r, sigma, "call")
    iv = implied_vol(price, spot, strike, t, r, "call")
    assert iv is not None
    assert abs(iv - sigma) < 1e-3


def test_iv_returns_none_for_zero_price():
    assert implied_vol(0.0, 100, 100, 0.25, 0.04, "call") is None
