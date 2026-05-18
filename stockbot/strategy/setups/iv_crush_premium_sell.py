"""High-IV premium-sell — sell defined-risk premium into elevated IV
with no catalyst inside the contract's DTE window.

Roadmap §13.2:
  IV rank > 70, no catalyst within DTE,
  defined-risk structure only (iron condor / vertical credit).

Instrument hint is `put` rather than `equity` because the gate's
downstream notification (Cluster 3) wants to flag this as an options
trade. The actual contract selection is the user's job — the gate is
not a trade-execution layer.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..scorer import TechnicalRead
from ...data.fundamentals import Fundamentals
from ...data.macro import MacroSnapshot
from .types import OptionsContext


@dataclass(frozen=True)
class IVCrushPremiumSell:
    name: str = "iv_crush_premium_sell"
    direction: str = "short"                # premium-sell is short-vol, not short-stock
    instrument_hint: str = "put"            # see module docstring
    iv_rank_floor: float = 70.0
    holding_days_min: int = 10
    holding_days_max: int = 30

    def matches(
        self,
        tech: TechnicalRead,
        fundamentals: Optional[Fundamentals],
        options: Optional[OptionsContext],
        macro: MacroSnapshot,
    ) -> bool:
        if options is None or options.iv_rank is None:
            return False                                # no chain → no match
        return options.iv_rank >= self.iv_rank_floor and not options.has_catalyst_within_dte

    def expected_holding_days(self) -> tuple[int, int]:
        return (self.holding_days_min, self.holding_days_max)
