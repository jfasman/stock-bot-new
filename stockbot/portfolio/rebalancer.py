"""Book-level rebalancer (roadmap §13.8).

Today sizing decides each position in isolation; this module is the pure
function that sets the *whole portfolio's* weights at once, taking the
current book and a set of candidate picks as input.

The function is fully pure. Persistence (the `rebalance_proposals` table),
the cadence trigger, and the approval workflow live in `ops/rebalance.py`
and `engine/paper.py`. Tests live alongside in `tests/test_rebalancer.py`.

Algorithms
----------
- ``equal_risk_contribution`` (default, risk parity): inverse-vol weights on
  the combined set of current positions and new picks. Diagonal approximation
  (assumes zero correlation) — matches the existing `sizing.risk_parity_weights`.
- ``vol_target_book``: scales the inverse-vol allocation so realized
  portfolio volatility hits ``cfg.target_vol``.
- ``mean_variance_with_views``: Black-Litterman lite — when picks carry a
  ``confidence_band``, treat the band midpoint as a return view weighted by
  band width. Falls back to plain MV (equal expected return) when no picks
  have bands.

Discipline norm parity
----------------------
The conviction gate's "grow needs gate, shrink skips" rule (§13.8 "Interaction
with conviction gate") is enforced in the caller, not here — the rebalancer
returns a proposal and the gate decides which grows survive.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional

from .constraints import ConstraintCheck, ConstraintConfig, ProposedPosition, check_constraints


@dataclass(frozen=True)
class BookPosition:
    """A currently-held position, expressed as a fraction-of-equity weight."""
    ticker: str
    weight: float
    sector: Optional[str] = None
    beta: Optional[float] = None


@dataclass(frozen=True)
class CandidatePick:
    """A new idea the rebalancer should consider sizing into.

    `confidence_band` is the conviction-gate band (low, high) used by the
    mean-variance-with-views algo. Optional — when None, the pick is treated
    as a neutral view (equal expected return with other picks).
    """
    ticker: str
    target_weight: float
    sector: Optional[str] = None
    beta: Optional[float] = None
    confidence_band: Optional[tuple[float, float]] = None


@dataclass(frozen=True)
class RebalanceConfig:
    algo: str = "equal_risk_contribution"
    target_vol: float = 0.15
    max_turnover_per_rebalance: float = 0.20
    # Default risk-free for MV objective; kept here so the algos stay pure.
    risk_free_rate: float = 0.0


@dataclass(frozen=True)
class WeightChange:
    ticker: str
    current_weight: float
    target_weight: float

    @property
    def delta(self) -> float:
        return self.target_weight - self.current_weight

    @property
    def action(self) -> str:
        """Human-readable label: open / close / grow / shrink / hold."""
        eps = 1e-6
        if abs(self.current_weight) < eps and abs(self.target_weight) >= eps:
            return "open"
        if abs(self.current_weight) >= eps and abs(self.target_weight) < eps:
            return "close"
        if self.target_weight > self.current_weight + eps:
            return "grow"
        if self.target_weight < self.current_weight - eps:
            return "shrink"
        return "hold"


@dataclass(frozen=True)
class RebalanceProposal:
    algo: str
    changes: List[WeightChange]
    raw_turnover: float          # turnover before the max_turnover cap
    capped_turnover: float       # turnover after the cap
    feasible: bool               # constraints satisfied
    notes: List[str] = field(default_factory=list)
    constraint_breaches: List[str] = field(default_factory=list)

    @property
    def grows(self) -> List[WeightChange]:
        return [c for c in self.changes if c.action in ("open", "grow")]

    @property
    def shrinks(self) -> List[WeightChange]:
        return [c for c in self.changes if c.action in ("close", "shrink")]


# ----------------------------------------------------------------------
# Algorithms
# ----------------------------------------------------------------------

def _inverse_vol_weights(tickers: List[str], vols: Optional[Mapping[str, float]]) -> Dict[str, float]:
    """Inverse-vol weights summing to 1. Missing vols default to mean vol (so a
    name with no history just becomes equal-weight relative to peers that do).
    """
    if not tickers:
        return {}
    if vols:
        known = [v for v in vols.values() if v and v > 0]
        fallback = sum(known) / len(known) if known else 1.0
        inv = {t: 1.0 / max(vols.get(t, fallback) or fallback, 1e-9) for t in tickers}
    else:
        inv = {t: 1.0 for t in tickers}
    s = sum(inv.values())
    return {t: (w / s if s > 0 else 0.0) for t, w in inv.items()}


def _equal_risk_contribution(
    tickers: List[str],
    vols: Optional[Mapping[str, float]],
) -> Dict[str, float]:
    return _inverse_vol_weights(tickers, vols)


def _vol_target_book(
    tickers: List[str],
    vols: Optional[Mapping[str, float]],
    target_vol: float,
) -> Dict[str, float]:
    """Inverse-vol allocation, scaled so portfolio vol ≈ target.

    Portfolio vol under the diagonal/zero-correlation assumption is
    sqrt(Σ w_i² σ_i²). Solving for a common scale s on inverse-vol weights:
    target = s × sqrt(Σ inv_i² σ_i²) → s = target / sqrt(...).
    The fraction left over after scaling stays in cash.
    """
    base = _inverse_vol_weights(tickers, vols)
    if not base or not vols:
        return base
    sigma_w_sq = sum((base[t] * vols.get(t, 0.0)) ** 2 for t in tickers if vols.get(t))
    if sigma_w_sq <= 0:
        return base
    book_vol = math.sqrt(sigma_w_sq)
    if book_vol <= 0:
        return base
    scale = min(1.0, target_vol / book_vol)
    return {t: w * scale for t, w in base.items()}


def _mean_variance_with_views(
    tickers: List[str],
    vols: Optional[Mapping[str, float]],
    views: Mapping[str, tuple[float, float]],
) -> Dict[str, float]:
    """Black-Litterman lite.

    For each name with a confidence band (lo, hi):
      - expected return = (lo + hi) / 2  (band midpoint)
      - confidence ∝ 1 / (hi - lo + ε)   (narrower band = more confidence)

    Plain MV would be w_i ∝ μ_i / σ_i²; we additionally multiply by the
    confidence weight so a pick with a tight band gets more sizing than one
    with a wide band even if they share a midpoint.

    With no views at all, falls back to inverse-vol (the plain-MV equivalent
    of "all expected returns equal" under the diagonal assumption).
    """
    if not tickers:
        return {}
    if not views:
        return _inverse_vol_weights(tickers, vols)

    raw: Dict[str, float] = {}
    for t in tickers:
        sigma = (vols.get(t) if vols else None) or 0.0
        var = sigma * sigma if sigma > 0 else 1.0
        v = views.get(t)
        if v is None:
            # Names without a view get a neutral, low-weight slot — keep them
            # in the book at inverse-vol size, but smaller than view-bearing names.
            raw[t] = (1.0 / sigma if sigma > 0 else 1.0) * 0.25
            continue
        lo, hi = v
        mu = 0.5 * (lo + hi)
        width = max(hi - lo, 1e-6)
        confidence = 1.0 / width
        raw[t] = max(0.0, mu) / var * confidence

    s = sum(raw.values())
    if s <= 0:
        return _inverse_vol_weights(tickers, vols)
    return {t: w / s for t, w in raw.items()}


# ----------------------------------------------------------------------
# Top-level entry point
# ----------------------------------------------------------------------

def _apply_position_cap(weights: Dict[str, float], cap: float) -> Dict[str, float]:
    """Cap any weight at `cap`, redistribute the excess proportionally to the
    remaining (uncapped) names. Iterates until stable or all names are capped.
    """
    if cap <= 0 or not weights:
        return weights
    w = dict(weights)
    for _ in range(len(w) + 1):  # bounded — at most one new cap-hit per pass
        excess = 0.0
        below: Dict[str, float] = {}
        capped_total = 0.0
        for t, x in w.items():
            if x > cap + 1e-12:
                excess += x - cap
                w[t] = cap
                capped_total += cap
            elif x >= cap - 1e-12:
                capped_total += x
            else:
                below[t] = x
        if excess <= 1e-12 or not below:
            break
        room = sum(below.values())
        if room <= 0:
            break
        for t in below:
            w[t] = below[t] + excess * (below[t] / room)
    return w


def _apply_turnover_cap(
    current: Dict[str, float],
    target: Dict[str, float],
    max_turnover: float,
) -> tuple[Dict[str, float], float, float]:
    """Scale targets toward `current` until the total |delta| stays within
    `max_turnover`. Returns (adjusted_target, raw_turnover, capped_turnover).
    """
    tickers = set(current) | set(target)
    raw = sum(abs(target.get(t, 0.0) - current.get(t, 0.0)) for t in tickers)
    if max_turnover <= 0 or raw <= max_turnover or raw == 0:
        return dict(target), raw, raw
    alpha = max_turnover / raw
    adjusted = {
        t: current.get(t, 0.0) + alpha * (target.get(t, 0.0) - current.get(t, 0.0))
        for t in tickers
    }
    # Drop tickers where adjusted weight is effectively zero AND wasn't held.
    adjusted = {t: w for t, w in adjusted.items() if not (abs(w) < 1e-9 and t not in current)}
    capped = sum(abs(adjusted.get(t, 0.0) - current.get(t, 0.0)) for t in tickers)
    return adjusted, raw, capped


def rebalance(
    book: Iterable[BookPosition],
    picks: Iterable[CandidatePick],
    cfg: RebalanceConfig,
    *,
    vols: Optional[Mapping[str, float]] = None,
    constraints: Optional[ConstraintConfig] = None,
) -> RebalanceProposal:
    """Compute a target-weight proposal from the current book + candidate picks.

    The proposal carries both raw and capped turnover so the caller can see
    what the algo wanted vs what the turnover cap allowed. Constraint feasibility
    is reported on the final (capped, position-capped) weights; an infeasible
    proposal is still returned so the audit log captures *what was attempted*.
    """
    book_list = list(book)
    picks_list = list(picks)
    notes: List[str] = []

    current_weights: Dict[str, float] = {p.ticker: p.weight for p in book_list}
    pick_lookup: Dict[str, CandidatePick] = {p.ticker: p for p in picks_list}
    universe: List[str] = sorted(set(current_weights) | set(pick_lookup))

    # Algorithm dispatch. Unknown algo falls back to ERC with a note rather
    # than raising — keeps the engine cycle robust to bad config typos.
    if cfg.algo == "equal_risk_contribution":
        target_weights = _equal_risk_contribution(universe, vols)
    elif cfg.algo == "vol_target_book":
        target_weights = _vol_target_book(universe, vols, cfg.target_vol)
    elif cfg.algo == "mean_variance_with_views":
        views = {
            p.ticker: p.confidence_band for p in picks_list if p.confidence_band is not None
        }
        target_weights = _mean_variance_with_views(universe, vols, views)
        if not views:
            notes.append("No confidence bands present; MV-with-views reduced to inverse-vol.")
    else:
        notes.append(f"Unknown algo '{cfg.algo}' — fell back to equal_risk_contribution.")
        target_weights = _equal_risk_contribution(universe, vols)

    # Position cap (from constraints, if supplied).
    if constraints is not None:
        target_weights = _apply_position_cap(target_weights, constraints.max_position_pct)

    # Turnover cap.
    capped, raw_turnover, capped_turnover = _apply_turnover_cap(
        current_weights, target_weights, cfg.max_turnover_per_rebalance
    )

    # Build change list — include held names whose target hits zero (closes).
    universe_with_closes = sorted(set(universe) | set(current_weights) | set(capped))
    changes = [
        WeightChange(
            ticker=t,
            current_weight=current_weights.get(t, 0.0),
            target_weight=capped.get(t, 0.0),
        )
        for t in universe_with_closes
    ]

    # Constraint check on the final proposal (uses sector/beta from inputs).
    sector_lookup: Dict[str, Optional[str]] = {}
    beta_lookup: Dict[str, Optional[float]] = {}
    for p in book_list:
        sector_lookup[p.ticker] = p.sector
        beta_lookup[p.ticker] = p.beta
    for p in picks_list:
        sector_lookup.setdefault(p.ticker, p.sector)
        beta_lookup.setdefault(p.ticker, p.beta)

    constraint_breaches: List[str] = []
    feasible = True
    if constraints is not None:
        check: ConstraintCheck = check_constraints(
            [
                ProposedPosition(
                    ticker=c.ticker,
                    sector=sector_lookup.get(c.ticker),
                    weight=c.target_weight,
                    beta=beta_lookup.get(c.ticker),
                )
                for c in changes
                if abs(c.target_weight) > 1e-9
            ],
            constraints,
        )
        feasible = check.ok
        constraint_breaches = list(check.breaches)
        if not feasible:
            notes.append("Constraints not satisfied — proposal logged but flagged infeasible.")

    return RebalanceProposal(
        algo=cfg.algo,
        changes=changes,
        raw_turnover=raw_turnover,
        capped_turnover=capped_turnover,
        feasible=feasible,
        notes=notes,
        constraint_breaches=constraint_breaches,
    )


def make_backtest_hook(
    cfg: RebalanceConfig,
    *,
    vol_loader: Optional[callable] = None,
):
    """Wrap the pure rebalancer in the (current_weights, signal_weights) →
    adjusted_weights signature the backtester expects.

    `vol_loader(ticker) -> float | None` supplies vols; when None, weights
    fall back to equal-vol (which still reduces to risk parity = equal weight).
    """

    def hook(current_weights: Dict[str, float], signal_weights: Dict[str, float]) -> Dict[str, float]:
        book = [BookPosition(t, w) for t, w in current_weights.items() if abs(w) > 1e-9]
        picks = [CandidatePick(t, target_weight=0.0) for t in signal_weights]
        vols: Dict[str, float] = {}
        if vol_loader is not None:
            for t in {*current_weights.keys(), *signal_weights.keys()}:
                try:
                    v = vol_loader(t)
                    if v and v > 0:
                        vols[t] = v
                except Exception:
                    pass
        proposal = rebalance(book, picks, cfg, vols=vols or None)
        return {c.ticker: c.target_weight for c in proposal.changes if c.target_weight > 0}

    return hook
