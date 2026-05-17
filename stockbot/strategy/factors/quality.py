from __future__ import annotations

from typing import Dict

from ...data.fundamentals import Fundamentals
from ..cross_section import rank_pct


def quality_score(fundamentals: Dict[str, Fundamentals]) -> Dict[str, float]:
    """Composite of ROE, gross margin, debt-to-equity (inverted).

    Note: ROIC would be ideal but requires invested-capital breakdown; ROE is the
    yfinance-available proxy. Replace with proper ROIC when a paid vendor lands.
    """
    roe = {t: f.return_on_equity for t, f in fundamentals.items()}
    gm = {t: f.gross_margin for t, f in fundamentals.items()}
    de = {t: f.debt_to_equity for t, f in fundamentals.items()}
    pm = {t: f.profit_margin for t, f in fundamentals.items()}

    r_roe = rank_pct(roe, higher_is_better=True)
    r_gm = rank_pct(gm, higher_is_better=True)
    r_de = rank_pct(de, higher_is_better=False)
    r_pm = rank_pct(pm, higher_is_better=True)

    out: Dict[str, float] = {}
    for t in fundamentals:
        parts = []
        if fundamentals[t].return_on_equity is not None:
            parts.append(r_roe[t])
        if fundamentals[t].gross_margin is not None:
            parts.append(r_gm[t])
        if fundamentals[t].debt_to_equity is not None:
            parts.append(r_de[t])
        if fundamentals[t].profit_margin is not None:
            parts.append(r_pm[t])
        out[t] = sum(parts) / len(parts) if parts else 0.0
    return out
