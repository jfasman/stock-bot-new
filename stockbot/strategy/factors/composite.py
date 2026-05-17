from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, Optional

import pandas as pd

from ...data.fundamentals import Fundamentals
from ..cross_section import sector_neutralize
from .lowvol import lowvol_score
from .meanrev import meanrev_score
from .momentum import momentum_score
from .quality import quality_score
from .size import size_score
from .value import value_score


@dataclass
class FactorWeights:
    value: float = 0.20
    momentum: float = 0.30
    quality: float = 0.20
    lowvol: float = 0.10
    size: float = 0.05
    meanrev: float = 0.15

    def normalized(self) -> "FactorWeights":
        total = self.value + self.momentum + self.quality + self.lowvol + self.size + self.meanrev
        if total == 0:
            return FactorWeights()
        return FactorWeights(
            value=self.value / total,
            momentum=self.momentum / total,
            quality=self.quality / total,
            lowvol=self.lowvol / total,
            size=self.size / total,
            meanrev=self.meanrev / total,
        )


@dataclass
class FactorReport:
    composite: Dict[str, float]
    breakdown: Dict[str, Dict[str, float]] = field(default_factory=dict)

    def top(self, n: int = 10) -> list[tuple[str, float]]:
        return sorted(self.composite.items(), key=lambda kv: kv[1], reverse=True)[:n]

    def bottom(self, n: int = 10) -> list[tuple[str, float]]:
        return sorted(self.composite.items(), key=lambda kv: kv[1])[:n]


def score_universe(
    tickers: Iterable[str],
    fundamentals: Dict[str, Fundamentals],
    price_history: Dict[str, pd.DataFrame],
    weights: Optional[FactorWeights] = None,
    sector_neutral: bool = True,
) -> FactorReport:
    """Score a universe across the six factors and return composite + breakdown.

    `fundamentals` and `price_history` must be keyed by uppercased ticker. Missing
    inputs degrade gracefully — that factor's contribution becomes 0 for that name.
    """
    tickers = [t.upper() for t in tickers]
    fundamentals = {t.upper(): fundamentals.get(t.upper(), Fundamentals(ticker=t.upper())) for t in tickers}
    price_history = {t.upper(): price_history.get(t.upper(), pd.DataFrame()) for t in tickers}

    w = (weights or FactorWeights()).normalized()

    v = value_score(fundamentals)
    m = momentum_score(price_history)
    q = quality_score(fundamentals)
    l = lowvol_score(price_history)
    s = size_score(fundamentals)
    mr = meanrev_score(price_history)

    if sector_neutral:
        sectors = {t: fundamentals[t].sector for t in tickers}
        v = sector_neutralize(v, sectors)
        m = sector_neutralize(m, sectors)
        q = sector_neutralize(q, sectors)
        l = sector_neutralize(l, sectors)
        mr = sector_neutralize(mr, sectors)

    composite = {}
    breakdown = {}
    for t in tickers:
        c = (
            w.value * v.get(t, 0.0)
            + w.momentum * m.get(t, 0.0)
            + w.quality * q.get(t, 0.0)
            + w.lowvol * l.get(t, 0.0)
            + w.size * s.get(t, 0.0)
            + w.meanrev * mr.get(t, 0.0)
        )
        composite[t] = float(c)
        breakdown[t] = {
            "value": v.get(t, 0.0),
            "momentum": m.get(t, 0.0),
            "quality": q.get(t, 0.0),
            "lowvol": l.get(t, 0.0),
            "size": s.get(t, 0.0),
            "meanrev": mr.get(t, 0.0),
        }
    return FactorReport(composite=composite, breakdown=breakdown)
