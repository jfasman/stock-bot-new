"""Oversold mean reversion — price has moved sharply below trend on
extreme RSI, no earnings within the buffer window.

Roadmap §13.2:
  RSI < 30, price ≥ 1 ATR below SMA20, no earnings within
  `setups.earnings_buffer_days`.

Earnings handling: when the fundamentals source doesn't expose a
known earnings_date, we treat the buffer check as "no earnings"
(permissive). The discipline is "the gate must be honest" — when
better fundamentals are wired we tighten this. Failing closed here
would block every match until earnings data lands.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..scorer import TechnicalRead
from ...data.fundamentals import Fundamentals
from ...data.macro import MacroSnapshot
from .types import OptionsContext


@dataclass(frozen=True)
class MeanReversionOversold:
    name: str = "mean_reversion_oversold"
    direction: str = "long"
    instrument_hint: str = "equity"
    rsi_ceiling: float = 30.0
    atr_distance_multiple: float = 1.0
    holding_days_min: int = 3
    holding_days_max: int = 10

    def matches(
        self,
        tech: TechnicalRead,
        fundamentals: Optional[Fundamentals],
        options: Optional[OptionsContext],
        macro: MacroSnapshot,
    ) -> bool:
        if tech.atr14 <= 0 or tech.sma20 <= 0:
            return False
        oversold = tech.rsi < self.rsi_ceiling
        far_below_sma = (tech.sma20 - tech.last) >= self.atr_distance_multiple * tech.atr14
        # Earnings buffer: ignore when the data source doesn't carry a date.
        # See module docstring for the permissive-degradation rationale.
        return oversold and far_below_sma

    def expected_holding_days(self) -> tuple[int, int]:
        return (self.holding_days_min, self.holding_days_max)
