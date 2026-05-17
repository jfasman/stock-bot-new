from stockbot.portfolio.constraints import (
    ConstraintConfig,
    ProposedPosition,
    check_constraints,
)


def test_within_limits_passes():
    cfg = ConstraintConfig()
    proposed = [
        ProposedPosition("AAPL", "tech", 0.06, beta=1.1),
        ProposedPosition("MSFT", "tech", 0.06, beta=1.0),
        ProposedPosition("JPM", "fin", 0.05, beta=0.9),
    ]
    result = check_constraints(proposed, cfg)
    assert result.ok, result.breaches


def test_oversize_position_flagged():
    cfg = ConstraintConfig(max_position_pct=0.05)
    proposed = [ProposedPosition("AAPL", "tech", 0.10, beta=1.0)]
    result = check_constraints(proposed, cfg)
    assert not result.ok
    assert any("AAPL" in b for b in result.breaches)


def test_sector_concentration_flagged():
    cfg = ConstraintConfig(max_sector_pct=0.10)
    proposed = [
        ProposedPosition("AAPL", "tech", 0.07),
        ProposedPosition("MSFT", "tech", 0.07),
    ]
    result = check_constraints(proposed, cfg)
    assert not result.ok
    assert any("sector tech" in b for b in result.breaches)


def test_gross_exposure_flagged():
    cfg = ConstraintConfig(max_gross_exposure=0.20)
    proposed = [
        ProposedPosition("AAPL", "tech", 0.15),
        ProposedPosition("AMD", "tech", -0.10),
    ]
    result = check_constraints(proposed, cfg)
    assert not result.ok
