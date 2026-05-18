"""Context assembler for the conviction gate.

`strategy/conviction.py` defines the gates as pure functions over
`(Idea, GateContext)`. This module is the impure layer that builds
`GateContext` from the live system — config, macro snapshot, audit
log, setup library, and (eventually) the factor model.

Status:
  - `matched_setup` is wired through `strategy/setups/matcher.py`
    when the caller supplies `tech` (and optionally `fundamentals`
    and `options`). When inputs aren't supplied, falls back to None
    (gate fails closed).
  - `factor_composite` is still always None (Cluster 4 fuses factors
    into the live score; until then the per-name cross-sectional run
    is too expensive to invoke per idea).
  - Data-quality flags are stubbed fresh; no fetch-time tracking
    exists in `data/prices.py` yet.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from ..config import Config
from ..data import macro as macro_data
from ..data.fundamentals import Fundamentals
from ..ops import conviction_log
from ..ops.setup_performance import get_performance
from .conviction import (
    ConvictionThresholds,
    GateContext,
    MacroSnapshot,
    MatchedSetup,
)
from .ideas import Idea
from .scorer import TechnicalRead
from .setups import Setup
from .setups.matcher import match, pick_best
from .setups.types import OptionsContext


def build_thresholds(cfg: Config) -> ConvictionThresholds:
    """Slice the config into the gate's threshold record."""
    c = cfg.conviction
    regime = c.get("regime", {})
    dq = c.get("data_quality", {})
    return ConvictionThresholds(
        notify_threshold=float(c.get("notify_threshold", 0.70)),
        min_score=abs(float(cfg.strategy.get("min_score_to_trade", 0.55))),
        cooldown_hours=float(c.get("cooldown_hours", 24)),
        min_setup_trades=int(c.get("min_setup_trades", 20)),
        setup_validation_max_age_days=int(c.get("setup_validation_max_age_days", 180)),
        vix_max_long=float(regime.get("vix_max_long", 28)),
        vix_max_short=float(regime.get("vix_max_short", 45)),
        yield_curve_inverted_blocks=tuple(regime.get("yield_curve_inverted_blocks", ())),
        vix_cap_exempt_setups=tuple(regime.get("vix_cap_exempt_setups", ())),
        max_price_age_seconds=float(dq.get("max_price_age_seconds", 900)),
        max_fundamentals_age_seconds=float(dq.get("max_fundamentals_age_seconds", 604800)),
    )


def to_gate_macro(raw: macro_data.MacroSnapshot) -> MacroSnapshot:
    """Map the raw (Optional-field) macro snapshot to the gate's strict
    record. Missing inputs collapse to defensive values: VIX defaults
    high (so long-vix-cap rejects), curve defaults flat (no
    inverted-curve block).
    """
    vix = raw.vix if raw.vix is not None else float("inf")
    curve = raw.yield_curve_2s10s if raw.yield_curve_2s10s is not None else 0.0
    return MacroSnapshot(vix=vix, yield_curve_slope_bps=curve)


def resolve_matched_setup(
    tech: Optional[TechnicalRead],
    fundamentals: Optional[Fundamentals],
    options: Optional[OptionsContext],
    macro: macro_data.MacroSnapshot,
) -> Optional[MatchedSetup]:
    """Run the setup matcher, pick the best by walk-forward
    expectancy, and wrap the result as a `MatchedSetup`.

    Returns None when:
      - `tech` isn't supplied (caller couldn't fetch indicator data)
      - no setup matches the inputs
      - the matched setup has no walk-forward row yet
        (setup_performance is empty for this name)

    Each `None` path is "fail closed" — the gate's
    setup_validated check rejects.
    """
    if tech is None:
        return None
    matches = match(tech, fundamentals, options, macro)
    if not matches:
        return None
    perf_lookup: dict[str, object] = {}
    for s in matches:
        perf = get_performance(s.name)
        if perf is not None:
            perf_lookup[s.name] = perf
    best: Optional[Setup] = pick_best(matches, perf_lookup)  # type: ignore[arg-type]
    if best is None:
        return None
    perf = perf_lookup.get(best.name)
    if perf is None:
        # Matched but no walk-forward data — gate will fail closed on
        # n_trades < min_setup_trades; surface that via a zero-row.
        return MatchedSetup(
            name=best.name, direction=best.direction,
            n_trades=0, expectancy=0.0,
            last_validated_at=datetime.min,
        )
    return MatchedSetup(
        name=best.name, direction=best.direction,
        n_trades=perf.n_trades, expectancy=perf.expectancy,
        last_validated_at=perf.last_validated_at,
    )


def build_context(
    cfg: Config,
    idea: Idea,
    *,
    now: Optional[datetime] = None,
    macro: Optional[macro_data.MacroSnapshot] = None,
    matched_setup: Optional[MatchedSetup] = None,
    factor_composite: Optional[float] = None,
    tech: Optional[TechnicalRead] = None,
    fundamentals: Optional[Fundamentals] = None,
    options: Optional[OptionsContext] = None,
) -> GateContext:
    """Build a GateContext for a single idea.

    Caller may pre-fetch `macro` to amortize cost across many ideas.
    When `tech` is supplied, the matcher runs automatically and
    `matched_setup` is derived from the highest-expectancy match
    (overriding any explicit `matched_setup` arg). Pass
    `matched_setup` explicitly when you want to bypass the matcher
    (tests, fixtures).
    """
    when = now or datetime.utcnow()
    raw_macro = macro if macro is not None else macro_data.snapshot()
    if matched_setup is None and tech is not None:
        matched_setup = resolve_matched_setup(tech, fundamentals, options, raw_macro)
    return GateContext(
        thresholds=build_thresholds(cfg),
        factor_composite=factor_composite,
        matched_setup=matched_setup,
        macro=to_gate_macro(raw_macro),
        last_notified_at=conviction_log.last_notified_at(idea.ticker),
        now=when,
        price_age_seconds=0.0,                  # stub: no fetch-time tracking yet
        has_unaccounted_corporate_action=False, # stub: corp-action audit not wired
        fundamentals_age_seconds=0.0,           # stub: fundamentals cache untimed
    )
