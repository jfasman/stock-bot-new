"""Tests for the conviction gate — scaffold. Spec: roadmap §13.1.

Each gate is unit-tested independently — toggle one input at a time
and assert the verdict. The orchestrator is then tested via a
synthetic Idea + GateContext fixture that exercises pass and fail.
"""
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from stockbot.strategy.conviction import (  # noqa: F401  (imports validate the public API)
    ConvictionPick,
    ConvictionThresholds,
    GateContext,
    GateResult,
    MacroSnapshot,
    MatchedSetup,
    TimeToAct,
    _cooldown_gate,
    _data_quality_gate,
    _factor_agreement_gate,
    _regime_gate,
    _score_gate,
    _setup_validated_gate,
    evaluate,
)
from stockbot.strategy.ideas import Idea


_NOT_IMPL = pytest.mark.skip(reason="scaffold — implement per roadmap §13.1")


def _make_idea(score: float = 0.80) -> Idea:
    return Idea(
        ticker="AAPL",
        direction="long",
        instrument="equity",
        score=score,
        last_price=200.0,
        rsi=55.0,
        reasons=["test"],
        sentiment_net=0.0,
        sentiment_confidence=0.0,
    )


def _make_ctx(**overrides) -> GateContext:
    now = datetime(2026, 5, 17, 14, 30)
    thresholds = ConvictionThresholds(
        notify_threshold=0.70,
        min_score=0.55,
        cooldown_hours=24,
        min_setup_trades=20,
        setup_validation_max_age_days=180,
        vix_max_long=28.0,
        vix_max_short=45.0,
        yield_curve_inverted_blocks=("breakout_with_momentum",),
        vix_cap_exempt_setups=("mean_reversion_oversold",),
        max_price_age_seconds=900.0,
        max_fundamentals_age_seconds=7 * 24 * 3600.0,
    )
    base = GateContext(
        thresholds=thresholds,
        factor_composite=0.4,
        matched_setup=MatchedSetup(
            name="pullback_in_uptrend",
            direction="long",
            n_trades=40,
            expectancy=0.25,
            last_validated_at=now - timedelta(days=10),
        ),
        macro=MacroSnapshot(vix=18.0, yield_curve_slope_bps=25.0, dxy_trend=-0.01),
        last_notified_at=None,
        now=now,
        price_age_seconds=60.0,
        has_unaccounted_corporate_action=False,
        fundamentals_age_seconds=3600.0,
    )
    return replace(base, **overrides) if overrides else base


def test_score_gate_rejects_below_notify_threshold():
    # |score| = 0.65 is above min_score (0.55) but below notify_threshold (0.70).
    idea = _make_idea(score=0.65)
    result = _score_gate(idea, _make_ctx())
    assert result.passed is False
    assert "notify_threshold" in result.reason


def test_score_gate_requires_strict_inequality_over_min_score():
    # |score| exactly equal to min_score should fail (strict >).
    # Raise notify_threshold to isolate the min_score check.
    ctx = _make_ctx(
        thresholds=replace(
            _make_ctx().thresholds,
            notify_threshold=0.40,
            min_score=0.55,
        )
    )
    equal = _score_gate(_make_idea(score=0.55), ctx)
    assert equal.passed is False
    assert "min_score" in equal.reason

    just_over = _score_gate(_make_idea(score=0.5501), ctx)
    assert just_over.passed is True

    # Symmetric for shorts: negative score with same magnitude behaves the same.
    short_equal = _score_gate(_make_idea(score=-0.55), ctx)
    assert short_equal.passed is False
    short_pass = _score_gate(_make_idea(score=-0.80), ctx)
    assert short_pass.passed is True


def test_factor_agreement_gate_rejects_on_direction_disagreement():
    # Live score bullish, factor composite bearish -> reject.
    long_idea = _make_idea(score=0.80)
    ctx = _make_ctx(factor_composite=-0.30)
    result = _factor_agreement_gate(long_idea, ctx)
    assert result.passed is False
    assert "disagreement" in result.reason

    # Symmetric: bearish live score, bullish factor -> reject.
    short_idea = _make_idea(score=-0.80)
    ctx_pos = _make_ctx(factor_composite=0.30)
    result = _factor_agreement_gate(short_idea, ctx_pos)
    assert result.passed is False


def test_factor_agreement_gate_fails_closed_when_factor_unavailable():
    # `None` factor_composite -> reject; "no opinion" is not agreement.
    result = _factor_agreement_gate(_make_idea(score=0.80), _make_ctx(factor_composite=None))
    assert result.passed is False
    assert "unavailable" in result.reason


def test_factor_agreement_gate_passes_when_signs_agree():
    # Both bullish.
    assert _factor_agreement_gate(_make_idea(score=0.80), _make_ctx(factor_composite=0.20)).passed
    # Both bearish.
    assert _factor_agreement_gate(_make_idea(score=-0.80), _make_ctx(factor_composite=-0.20)).passed


def test_regime_gate_rejects_high_vix_long_breakout():
    # vix_max_long=28; breakout setup; VIX=35 -> reject.
    base = _make_ctx()
    ctx = _make_ctx(
        matched_setup=replace(
            base.matched_setup, name="breakout_with_momentum", direction="long"
        ),
        macro=replace(base.macro, vix=35.0),
    )
    result = _regime_gate(_make_idea(), ctx)
    assert result.passed is False
    assert "VIX" in result.reason


def test_regime_gate_allows_mean_reversion_in_higher_vol():
    # Same VIX=35 that rejects a breakout passes for mean-reversion (exempt).
    base = _make_ctx()
    ctx = _make_ctx(
        matched_setup=replace(
            base.matched_setup, name="mean_reversion_oversold", direction="long"
        ),
        macro=replace(base.macro, vix=35.0),
    )
    result = _regime_gate(_make_idea(), ctx)
    assert result.passed is True
    assert "exempt" in result.reason


def test_regime_gate_rejects_blocked_setup_on_inverted_curve():
    # Yield-curve inverted (slope < 0) and setup is on the blocklist -> reject.
    base = _make_ctx()
    ctx = _make_ctx(
        matched_setup=replace(
            base.matched_setup, name="breakout_with_momentum", direction="long"
        ),
        macro=replace(base.macro, yield_curve_slope_bps=-15.0),
    )
    result = _regime_gate(_make_idea(), ctx)
    assert result.passed is False
    assert "yield curve" in result.reason


def test_setup_validated_gate_fails_closed_on_insufficient_sample():
    # min_setup_trades=20; n=19 -> reject.
    base = _make_ctx()
    ctx = _make_ctx(matched_setup=replace(base.matched_setup, n_trades=19))
    result = _setup_validated_gate(_make_idea(), ctx)
    assert result.passed is False
    assert "insufficient sample" in result.reason


def test_setup_validated_gate_rejects_stale_validation_window():
    # setup_validation_max_age_days=180; validated 200d ago -> reject.
    base = _make_ctx()
    ctx = _make_ctx(
        matched_setup=replace(
            base.matched_setup,
            last_validated_at=base.now - timedelta(days=200),
        )
    )
    result = _setup_validated_gate(_make_idea(), ctx)
    assert result.passed is False
    assert "stale validation" in result.reason


def test_setup_validated_gate_fails_closed_when_no_setup_matched():
    result = _setup_validated_gate(_make_idea(), _make_ctx(matched_setup=None))
    assert result.passed is False
    assert "no matched setup" in result.reason


def test_setup_validated_gate_rejects_non_positive_expectancy():
    base = _make_ctx()
    ctx = _make_ctx(matched_setup=replace(base.matched_setup, expectancy=0.0))
    result = _setup_validated_gate(_make_idea(), ctx)
    assert result.passed is False
    assert "non-positive" in result.reason


def test_setup_validated_gate_passes_with_validated_setup():
    assert _setup_validated_gate(_make_idea(), _make_ctx()).passed is True


def test_cooldown_gate_blocks_within_cooldown_window():
    ctx = _make_ctx(last_notified_at=_make_ctx().now - timedelta(hours=6))  # cooldown=24h
    result = _cooldown_gate(_make_idea(), ctx)
    assert result.passed is False
    assert "cooldown" in result.reason


def test_cooldown_gate_passes_when_never_notified():
    result = _cooldown_gate(_make_idea(), _make_ctx(last_notified_at=None))
    assert result.passed is True


def test_cooldown_gate_passes_at_exact_window_boundary():
    # elapsed == cooldown_hours should pass (cooldown has expired).
    base = _make_ctx()
    ctx = _make_ctx(last_notified_at=base.now - timedelta(hours=base.thresholds.cooldown_hours))
    assert _cooldown_gate(_make_idea(), ctx).passed is True


def test_data_quality_gate_rejects_stale_price():
    # price_age beyond threshold -> reject with a price-specific reason.
    ctx = _make_ctx(price_age_seconds=10_000.0)  # threshold is 900s
    result = _data_quality_gate(_make_idea(), ctx)
    assert result.passed is False
    assert "price stale" in result.reason


def test_data_quality_gate_rejects_unaccounted_corporate_action():
    ctx = _make_ctx(has_unaccounted_corporate_action=True)
    result = _data_quality_gate(_make_idea(), ctx)
    assert result.passed is False
    assert "corporate action" in result.reason


def test_data_quality_gate_rejects_stale_fundamentals():
    ctx = _make_ctx(fundamentals_age_seconds=30 * 24 * 3600.0)  # 30d, threshold is 7d
    result = _data_quality_gate(_make_idea(), ctx)
    assert result.passed is False
    assert "fundamentals stale" in result.reason


def test_data_quality_gate_passes_when_all_fresh():
    assert _data_quality_gate(_make_idea(), _make_ctx()).passed is True


_ALL_GATES = ("score", "factor_agreement", "regime", "setup_validated", "cooldown", "data_quality")


def test_evaluate_returns_pick_when_all_gates_pass():
    pick, verdicts = evaluate(_make_idea(score=0.80), _make_ctx())
    assert pick is not None
    assert isinstance(pick, ConvictionPick)
    assert pick.idea.ticker == "AAPL"
    assert pick.gates_passed == _ALL_GATES
    # Confidence band spans the live score and the factor composite.
    assert pick.confidence_band == (0.4, 0.80)
    assert pick.time_to_act is TimeToAct.OPEN_TOMORROW
    assert all(v.passed for v in verdicts.values())


def test_evaluate_returns_none_with_full_verdict_map_on_any_failure():
    # Trip a single gate (cooldown) and confirm the orchestrator returns None
    # but still produces a full verdict map.
    base = _make_ctx()
    ctx = _make_ctx(last_notified_at=base.now - timedelta(hours=1))
    pick, verdicts = evaluate(_make_idea(score=0.80), ctx)
    assert pick is None
    assert set(verdicts) == set(_ALL_GATES)
    assert verdicts["cooldown"].passed is False
    # The other gates still ran (not short-circuited).
    assert verdicts["score"].passed is True
    assert verdicts["data_quality"].passed is True


def test_evaluate_verdict_map_always_contains_every_gate():
    # Even with multiple failures, every gate appears in the verdict map.
    ctx = _make_ctx(
        factor_composite=-0.30,                      # factor_agreement fail
        price_age_seconds=10_000.0,                  # data_quality fail
        matched_setup=None,                          # setup_validated fail
    )
    _, verdicts = evaluate(_make_idea(score=0.30), ctx)  # score fail (below notify)
    assert set(verdicts) == set(_ALL_GATES)
    assert verdicts["score"].passed is False
    assert verdicts["factor_agreement"].passed is False
    assert verdicts["setup_validated"].passed is False
    assert verdicts["data_quality"].passed is False
