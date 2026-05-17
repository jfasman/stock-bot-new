"""Tests for the conviction gate — scaffold. Spec: roadmap §13.1.

Each gate is unit-tested independently — toggle one input at a time
and assert the verdict. The orchestrator is then tested via a
synthetic Idea + GateContext fixture that exercises pass and fail.
"""
import pytest

from stockbot.strategy.conviction import (  # noqa: F401  (imports validate the public API)
    ConvictionPick,
    GateContext,
    GateResult,
    TimeToAct,
    evaluate,
)


pytestmark = pytest.mark.skip(reason="scaffold — implement per roadmap §13.1")


def test_score_gate_rejects_below_notify_threshold():
    pass


def test_score_gate_requires_strict_inequality_over_min_score():
    pass


def test_factor_agreement_gate_rejects_on_direction_disagreement():
    pass


def test_regime_gate_rejects_high_vix_long_breakout():
    pass


def test_regime_gate_allows_mean_reversion_in_higher_vol():
    pass


def test_setup_validated_gate_fails_closed_on_insufficient_sample():
    pass


def test_setup_validated_gate_rejects_stale_validation_window():
    pass


def test_cooldown_gate_blocks_within_cooldown_window():
    pass


def test_data_quality_gate_rejects_stale_price():
    pass


def test_evaluate_returns_pick_when_all_gates_pass():
    pass


def test_evaluate_returns_none_with_full_verdict_map_on_any_failure():
    pass


def test_evaluate_verdict_map_always_contains_every_gate():
    pass
