"""Conviction gate — scaffold.

Second, stricter layer between "idea generated" and "user alerted."
Spec: roadmap-section.md §13.1.

Pure functions over data, no side effects. Logging is the caller's job
(persist the returned verdict map to a `conviction_log` table — schema
TBD per roadmap §13.1 "Logging").
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
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
class MacroSnapshot:
    """Gate-facing macro inputs. Distinct from `data.macro.MacroSnapshot`,
    which is the broader (Optional-field) raw snapshot — the context
    assembler maps from that to this.
    """
    vix: float
    yield_curve_slope_bps: float   # 10y - 2y in bps; negative = inverted


@dataclass(frozen=True)
class MatchedSetup:
    name: str
    direction: str                 # "long" | "short"
    n_trades: int
    expectancy: float              # avg R per trade
    last_validated_at: datetime


@dataclass(frozen=True)
class ConvictionThresholds:
    notify_threshold: float                       # default 0.70
    min_score: float                              # mirror of strategy.min_score_to_trade (abs value)
    cooldown_hours: float
    min_setup_trades: int
    setup_validation_max_age_days: int
    vix_max_long: float
    vix_max_short: float
    yield_curve_inverted_blocks: tuple[str, ...]  # setup names blocked when inverted
    vix_cap_exempt_setups: tuple[str, ...]        # setup names exempt from the VIX cap
    max_price_age_seconds: float
    max_fundamentals_age_seconds: float


@dataclass(frozen=True)
class GateContext:
    """Inputs the gates need beyond the Idea itself. Pure data; the
    caller (engine/paper.py after generate_ideas) owns assembly.
    """
    thresholds: ConvictionThresholds
    factor_composite: float | None          # None => factor model has no opinion (fail closed)
    matched_setup: MatchedSetup | None      # None => no validated setup (fail closed)
    macro: MacroSnapshot
    last_notified_at: datetime | None       # None => never notified for this ticker
    now: datetime                           # injected for determinism
    price_age_seconds: float
    has_unaccounted_corporate_action: bool
    fundamentals_age_seconds: float


def _score_gate(idea: Idea, ctx: GateContext) -> GateResult:
    """|score| ≥ conviction.notify_threshold AND |score| > strategy.min_score.

    Symmetric in sign: puts and inverse-ETF ideas carry negative
    `idea.score`, so we compare magnitudes against the (positive)
    thresholds. Strict inequality over `min_score` matches the roadmap
    wording "strictly greater than `strategy.min_score`."
    """
    magnitude = abs(idea.score)
    notify = ctx.thresholds.notify_threshold
    floor = ctx.thresholds.min_score
    if magnitude < notify:
        return GateResult(False, f"|score|={magnitude:.3f} < notify_threshold={notify:.3f}")
    if magnitude <= floor:
        return GateResult(False, f"|score|={magnitude:.3f} not > min_score={floor:.3f}")
    return GateResult(True, f"|score|={magnitude:.3f} ≥ {notify:.3f} and > {floor:.3f}")


def _factor_agreement_gate(idea: Idea, ctx: GateContext) -> GateResult:
    """Factor composite must agree on direction with the live score.

    Fails closed when the factor model has no opinion
    (`factor_composite is None`, or exactly zero) — "no agreement" is
    not "agreement." A zero live score would already have been
    rejected by the score gate; we still guard it defensively.
    """
    fc = ctx.factor_composite
    if fc is None:
        return GateResult(False, "factor_composite unavailable")
    live = idea.score
    if fc == 0.0 or live == 0.0:
        return GateResult(False, f"no directional opinion (live={live:+.3f}, factor={fc:+.3f})")
    if (live > 0) != (fc > 0):
        return GateResult(False, f"direction disagreement (live={live:+.3f}, factor={fc:+.3f})")
    return GateResult(True, f"agree (live={live:+.3f}, factor={fc:+.3f})")


def _regime_gate(idea: Idea, ctx: GateContext) -> GateResult:
    """Macro snapshot inside configured bounds for the setup type.

    Two checks:
      1. Yield-curve blocklist — if the curve is inverted and the
         matched setup is on `yield_curve_inverted_blocks`, reject.
      2. VIX cap — direction-aware (`vix_max_long` vs `vix_max_short`),
         with a name-based exemption list so mean-reversion setups
         (which want high vol) can pass when momentum/breakout setups
         would not.

    Direction comes from the matched setup when available, falling
    back to `idea.direction` so the gate still works in Cluster 1's
    "no setup library yet" state.
    """
    s = ctx.matched_setup
    if s is not None and ctx.macro.yield_curve_slope_bps < 0 and s.name in ctx.thresholds.yield_curve_inverted_blocks:
        return GateResult(False, f"yield curve inverted; {s.name} blocked")
    if s is not None and s.name in ctx.thresholds.vix_cap_exempt_setups:
        return GateResult(True, f"{s.name} exempt from VIX cap")
    direction = s.direction if s is not None else idea.direction
    cap = ctx.thresholds.vix_max_long if direction == "long" else ctx.thresholds.vix_max_short
    if ctx.macro.vix > cap:
        return GateResult(False, f"VIX {ctx.macro.vix:.1f} > {direction} cap {cap:.1f}")
    return GateResult(True, f"VIX {ctx.macro.vix:.1f} within {direction} cap {cap:.1f}")


def _setup_validated_gate(idea: Idea, ctx: GateContext) -> GateResult:
    """Idea must match a named setup with positive walk-forward
    expectancy and sufficient sample size. Fails closed.

    Four failure modes, all "no alert": no setup matched, sample too
    small, non-positive expectancy, validation window stale. Roadmap
    is explicit: "never alert on an unvalidated setup."
    """
    s = ctx.matched_setup
    if s is None:
        return GateResult(False, "no matched setup")
    if s.n_trades < ctx.thresholds.min_setup_trades:
        return GateResult(
            False,
            f"insufficient sample (n={s.n_trades} < {ctx.thresholds.min_setup_trades})",
        )
    if s.expectancy <= 0:
        return GateResult(False, f"non-positive expectancy ({s.expectancy:+.3f}R)")
    max_age = timedelta(days=ctx.thresholds.setup_validation_max_age_days)
    age = ctx.now - s.last_validated_at
    if age > max_age:
        return GateResult(False, f"stale validation (age {age} > {max_age})")
    return GateResult(True, f"{s.name} validated (n={s.n_trades}, E={s.expectancy:+.3f}R)")


def _cooldown_gate(idea: Idea, ctx: GateContext) -> GateResult:
    """No notification within conviction.cooldown_hours for this ticker.

    `last_notified_at is None` means the caller found no prior
    notification row for this ticker — pass. The boundary is inclusive
    on the pass side: elapsed exactly equal to `cooldown_hours` is OK
    (the cooldown has expired).
    """
    if ctx.last_notified_at is None:
        return GateResult(True, "no prior notification")
    window = timedelta(hours=ctx.thresholds.cooldown_hours)
    elapsed = ctx.now - ctx.last_notified_at
    if elapsed < window:
        remaining = window - elapsed
        return GateResult(False, f"within cooldown ({remaining} remaining of {window})")
    return GateResult(True, f"cooldown elapsed ({elapsed} ≥ {window})")


def _data_quality_gate(idea: Idea, ctx: GateContext) -> GateResult:
    """No stale prices, no recent unaccounted corporate action,
    fundamentals cache fresh.

    Three independent checks; first failure short-circuits with a
    specific reason so the audit row points at the actual problem.
    """
    if ctx.price_age_seconds > ctx.thresholds.max_price_age_seconds:
        return GateResult(
            False,
            f"price stale ({ctx.price_age_seconds:.0f}s > {ctx.thresholds.max_price_age_seconds:.0f}s)",
        )
    if ctx.has_unaccounted_corporate_action:
        return GateResult(False, "unaccounted corporate action")
    if ctx.fundamentals_age_seconds > ctx.thresholds.max_fundamentals_age_seconds:
        return GateResult(
            False,
            f"fundamentals stale ({ctx.fundamentals_age_seconds:.0f}s > {ctx.thresholds.max_fundamentals_age_seconds:.0f}s)",
        )
    return GateResult(True, "data fresh")


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
    `conviction_log` regardless of outcome. Every gate runs even after
    a failure; we want the full audit, not a short-circuit.
    """
    verdicts: dict[str, GateResult] = {name: gate(idea, ctx) for name, gate in GATES}
    if not all(v.passed for v in verdicts.values()):
        return None, verdicts

    # All gates passed. Build the pick. Confidence band is the range
    # spanned by the live score and the factor composite — the two
    # numeric opinions the gate just confirmed agree. `time_to_act` is
    # a placeholder until Cluster 2's setup library exposes
    # `expected_holding_days`.
    fc = ctx.factor_composite if ctx.factor_composite is not None else idea.score
    lo, hi = sorted((idea.score, fc))
    pick = ConvictionPick(
        idea=idea,
        gates_passed=tuple(name for name, _ in GATES),
        confidence_band=(lo, hi),
        time_to_act=TimeToAct.OPEN_TOMORROW,
    )
    return pick, verdicts
