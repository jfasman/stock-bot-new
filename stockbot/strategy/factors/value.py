from __future__ import annotations

from typing import Dict

from ...data.fundamentals import Fundamentals
from ..cross_section import rank_pct


def value_score(fundamentals: Dict[str, Fundamentals]) -> Dict[str, float]:
    """Composite value score from P/E, P/B, EV/EBITDA, FCF yield.

    Lower P/E, P/B, EV/EBITDA are better; higher FCF yield is better.
    Each is cross-sectionally ranked in [-1, 1], then averaged equal-weight
    across whichever metrics are available per ticker.
    """
    pe = {t: f.trailing_pe for t, f in fundamentals.items()}
    pb = {t: f.price_to_book for t, f in fundamentals.items()}
    ev = {t: f.ev_to_ebitda for t, f in fundamentals.items()}
    fcfy = {t: f.fcf_yield for t, f in fundamentals.items()}

    pe_r = rank_pct(pe, higher_is_better=False)
    pb_r = rank_pct(pb, higher_is_better=False)
    ev_r = rank_pct(ev, higher_is_better=False)
    fcf_r = rank_pct(fcfy, higher_is_better=True)

    out: Dict[str, float] = {}
    for t in fundamentals:
        components = []
        if fundamentals[t].trailing_pe is not None:
            components.append(pe_r[t])
        if fundamentals[t].price_to_book is not None:
            components.append(pb_r[t])
        if fundamentals[t].ev_to_ebitda is not None:
            components.append(ev_r[t])
        if fundamentals[t].fcf_yield is not None:
            components.append(fcf_r[t])
        out[t] = sum(components) / len(components) if components else 0.0
    return out
