from stockbot.portfolio.sizing import (
    SizingInput,
    kelly_fraction,
    kelly_size,
    risk_parity_weights,
    vol_target,
)


def test_kelly_fraction_zero_variance_returns_zero():
    assert kelly_fraction(edge=0.1, variance=0.0) == 0.0


def test_kelly_fraction_quarter_kelly():
    # Full Kelly = 0.1 / 0.04 = 2.5; quarter = 0.625
    f = kelly_fraction(edge=0.1, variance=0.04, fraction=0.25)
    assert abs(f - 0.625) < 1e-9


def test_kelly_size_respects_cap():
    out = kelly_size(SizingInput(edge=0.5, variance=0.01, price=50, equity=100_000), cap_weight=0.10)
    assert out.weight == 0.10
    assert out.dollars == 10_000
    assert out.shares == 200


def test_vol_target_scales_to_target():
    out = vol_target(equity=100_000, price=50, target_vol=0.10, asset_vol=0.20)
    assert abs(out.weight - 0.5) < 1e-9


def test_risk_parity_inverse_vol():
    w = risk_parity_weights([0.2, 0.1, 0.05])
    assert w[2] > w[1] > w[0]
    assert abs(sum(w) - 1.0) < 1e-9
