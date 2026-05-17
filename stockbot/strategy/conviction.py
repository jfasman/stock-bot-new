"""Conviction gate — scaffold.

Second, stricter layer between "idea generated" and "user alerted."
Spec: roadmap-section.md §13.1.

Pure functions over data, no side effects. Logging is the caller's job
(persist the returned verdict map to a `conviction_log` table — schema
TBD per roadmap §13.1 "Logging").
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import NamedTuple

from stockbot.strategy.ideas import Idea


class TimeToAct(str, Enum):
    OPEN_TOMORROW = "open_tomorrow"
    THIS_WEEK = "this_week"
    OPPORTUNISTIC = "opportunistic"


class GateResult(NamedTuple):
    passed: bool
    reason: str


@dataclass(frozen=True)
class ConvictionPick:
    idea: Idea
    gates_passed: tuple[str, ...]
    confidence_band: tuple[float, float]
    time_to_act: TimeToAct


@dataclass(frozen=True)
class GateContext:
    """Inputs the gates need beyond the Idea itself.

    Fill in concrete fields when wiring the first call site (likely in
    `engine/paper.py` after `generate_ideas`). Expected shape per
    roadmap §13.1: composite_score, factor_composite, macro snapshot,
    matched setup name + walk-forward expectancy, last notification ts
    for this ticker, data-freshness flags.
    """
    pass


def _score_gate(idea: Idea, ctx: GateContext) -> GateResult:
    """Composite score ≥ conviction.notify_threshold AND > strategy.min_score."""
    raise NotImplementedError


def _factor_agreement_gate(idea: Idea, ctx: GateContext) -> GateResult:
    """Factor composite must agree on direction with the live score."""
    raise NotImplementedError


def _regime_gate(idea: Idea, ctx: GateContext) -> GateResult:
    """Macro snapshot inside configured bounds for the setup type."""
    raise NotImplementedError


def _setup_validated_gate(idea: Idea, ctx: GateContext) -> GateResult:
    """Idea must match a named setup with positive walk-forward
    expectancy and sufficient sample size. Fails closed."""
    raise NotImplementedError


def _cooldown_gate(idea: Idea, ctx: GateContext) -> GateResult:
    """No notification within conviction.cooldown_hours for this ticker."""
    raise NotImplementedError


def _data_quality_gate(idea: Idea, ctx: GateContext) -> GateResult:
    """No stale prices, no recent unaccounted corporate action,
    fundamentals cache fresh."""
    raise NotImplementedError


GATES: tuple[tuple[str, object], ...] = (
    ("score", _score_gate),
    ("factor_agreement", _factor_agreement_gate),
    ("regime", _regime_gate),
    ("setup_validated", _setup_validated_gate),
    ("cooldown", _cooldown_gate),
    ("data_quality", _data_quality_gate),
)


def evaluate(
    idea: Idea,
    ctx: GateContext,
) -> tuple[ConvictionPick | None, dict[str, GateResult]]:
    """Run every gate. Return (pick_or_None, verdict_map).

    `verdict_map` contains a GateResult for every gate — pass *or*
    fail — so the caller can persist a full audit row to
    `conviction_log` regardless of outcome.
    """
    raise NotImplementedError
