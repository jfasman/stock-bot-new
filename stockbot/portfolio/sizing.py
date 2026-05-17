from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional


@dataclass
class SizingInput:
    edge: float            # expected return per unit time (decimal, e.g. 0.05)
    variance: float        # variance of returns (decimal)
    price: float
    equity: float


@dataclass
class SizingOutput:
    weight: float          # fraction of equity to allocate
    dollars: float
    shares: float
    rationale: str


def kelly_fraction(edge: float, variance: float, fraction: float = 0.25) -> float:
    """Fractional Kelly. Full Kelly = edge / variance. Default to 25%.

    `fraction` of 0.25 is the standard 'quarter Kelly' deployment — full Kelly is
    too aggressive in practice because edge and variance are noisy estimates.
    """
    if variance <= 0:
        return 0.0
    raw = edge / variance
    return max(0.0, fraction * raw)


def vol_target(equity: float, price: float, target_vol: float, asset_vol: float) -> SizingOutput:
    """Size so the position contributes `target_vol` annual stdev of NAV.

    weight = target_vol / asset_vol, capped at 1.0.
    """
    if asset_vol <= 0 or price <= 0:
        return SizingOutput(weight=0.0, dollars=0.0, shares=0.0, rationale="invalid inputs")
    weight = min(1.0, target_vol / asset_vol)
    dollars = equity * weight
    shares = dollars / price
    return SizingOutput(
        weight=weight,
        dollars=dollars,
        shares=shares,
        rationale=f"vol-target {target_vol:.2%} / asset vol {asset_vol:.2%}",
    )


def risk_parity_weights(vols: Iterable[float]) -> list[float]:
    """Inverse-vol weights summing to 1. Diagonal approximation — assumes zero correlation."""
    vols = list(vols)
    inv = [1.0 / v if v > 0 else 0.0 for v in vols]
    s = sum(inv)
    return [x / s if s > 0 else 0.0 for x in inv]


def kelly_size(inputs: SizingInput, fraction: float = 0.25, cap_weight: float = 0.10) -> SizingOutput:
    """Apply fractional Kelly with a hard weight cap."""
    f = min(cap_weight, kelly_fraction(inputs.edge, inputs.variance, fraction))
    dollars = inputs.equity * f
    shares = dollars / inputs.price if inputs.price > 0 else 0.0
    return SizingOutput(
        weight=f,
        dollars=dollars,
        shares=shares,
        rationale=f"{fraction:.0%}-Kelly (edge {inputs.edge:.2%}, var {inputs.variance:.4f}, cap {cap_weight:.0%})",
    )
