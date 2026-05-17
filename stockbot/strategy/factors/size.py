from __future__ import annotations

import math
from typing import Dict

from ...data.fundamentals import Fundamentals
from ..cross_section import rank_pct


def size_score(fundamentals: Dict[str, Fundamentals]) -> Dict[str, float]:
    """Size factor on log-market-cap. Small > large (academic size premium)."""
    caps: Dict[str, float | None] = {}
    for t, f in fundamentals.items():
        mc = f.market_cap
        caps[t] = math.log(mc) if (mc is not None and mc > 0) else None
    return rank_pct(caps, higher_is_better=False)
