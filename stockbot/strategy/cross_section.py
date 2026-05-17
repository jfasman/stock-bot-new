from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import pandas as pd


def rank_pct(values: Dict[str, Optional[float]], higher_is_better: bool = True) -> Dict[str, float]:
    """Convert raw factor values to cross-sectional percentile ranks in [-1, 1].

    Missing values map to 0 (neutral). With `higher_is_better=False`, the sign
    is flipped so e.g. low-P/E becomes a positive score.
    """
    items = [(k, v) for k, v in values.items() if v is not None and np.isfinite(v)]
    if not items:
        return {k: 0.0 for k in values}
    keys, raw = zip(*items)
    s = pd.Series(raw, index=keys)
    pct = s.rank(pct=True)
    centered = 2.0 * pct - 1.0
    if not higher_is_better:
        centered = -centered
    out: Dict[str, float] = {k: 0.0 for k in values}
    for k, v in centered.items():
        out[k] = float(v)
    return out


def zscore(values: Dict[str, Optional[float]], clip: float = 3.0) -> Dict[str, float]:
    """Cross-sectional z-score, clipped to ±`clip`. Missing -> 0."""
    items = [(k, v) for k, v in values.items() if v is not None and np.isfinite(v)]
    if not items:
        return {k: 0.0 for k in values}
    keys, raw = zip(*items)
    arr = np.array(raw, dtype=float)
    mu = arr.mean()
    sd = arr.std(ddof=0)
    if sd == 0:
        return {k: 0.0 for k in values}
    out: Dict[str, float] = {k: 0.0 for k in values}
    for k, v in zip(keys, arr):
        z = (v - mu) / sd
        out[k] = float(max(-clip, min(clip, z)))
    return out


def sector_neutralize(
    scores: Dict[str, float],
    sectors: Dict[str, Optional[str]],
) -> Dict[str, float]:
    """Subtract the sector mean from each ticker's score so cross-sector
    comparisons don't reward whole-sector trends.
    """
    by_sector: dict[str, list[tuple[str, float]]] = {}
    for t, s in scores.items():
        sec = sectors.get(t) or "_UNKNOWN_"
        by_sector.setdefault(sec, []).append((t, s))
    out = dict(scores)
    for sec, members in by_sector.items():
        if len(members) <= 1:
            continue
        mean = sum(s for _, s in members) / len(members)
        for t, _ in members:
            out[t] = out[t] - mean
    return out
