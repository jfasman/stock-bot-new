"""Context assembler for the conviction gate.

`strategy/conviction.py` defines the gates as pure functions over
`(Idea, GateContext)`. This module is the impure layer that builds
`GateContext` from the live system — config, macro snapshot, audit
log, and (eventually) the setup library and factor model.

Cluster 1 limitations (each documented at its stub):
  - `matched_setup` is always None (Cluster 2 ships the setup library)
  - `factor_composite` is always None (Cluster 4 fuses factors into
    the live score; until then the per-name cross-sectional run is
    too expensive to invoke per idea)
  - Data-quality flags are stubbed fresh; no fetch-time tracking
    exists in `data/prices.py` yet

Both `None`s cause their respective gates to fail closed, which is
the roadmap's "never alert on an unvalidated setup" / "no agreement is
not agreement" discipline. The audit row still shows why.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from ..config import Config
from ..data import macro as macro_data
from ..ops import conviction_log
from .conviction import (
    ConvictionThresholds,
    GateContext,
    MacroSnapshot,
    MatchedSetup,
)
from .ideas import Idea


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


def build_context(
    cfg: Config,
    idea: Idea,
    *,
    now: Optional[datetime] = None,
    macro: Optional[macro_data.MacroSnapshot] = None,
    matched_setup: Optional[MatchedSetup] = None,
    factor_composite: Optional[float] = None,
) -> GateContext:
    """Build a GateContext for a single idea.

    Caller may pre-fetch `macro` to amortize cost across many ideas.
    `matched_setup` and `factor_composite` are caller-injectable for
    forward-compat with Cluster 2 (setup library) and Cluster 4
    (factor fusion); both default to None and fail their gates closed.
    """
    when = now or datetime.utcnow()
    raw_macro = macro if macro is not None else macro_data.snapshot()
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
