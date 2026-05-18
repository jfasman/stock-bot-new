"""Trend-continuation breakout — strong move out of a 20-day range,
confirmed by RSI / MACD / volume.

Roadmap §13.2:
  close > 20d high, RSI > 60, MACD histogram positive,
  volume ≥ 1.5× 20d avg.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..scorer import TechnicalRead
from ...data.fundamentals import Fundamentals
from ...data.macro import MacroSnapshot
from .types import OptionsContext


@dataclass(frozen=True)
class BreakoutWithMomentum:
    name: str = "breakout_with_momentum"
    direction: str = "long"
    instrument_hint: str = "equity"
    rsi_floor: float = 60.0
    volume_multiple: float = 1.5
    holding_days_min: int = 5
    holding_days_max: int = 15

    def matches(
        self,
        tech: TechnicalRead,
        fundamentals: Optional[Fundamentals],
        options: Optional[OptionsContext],
        macro: MacroSnapshot,
    ) -> bool:
        if tech.high_20d <= 0 or tech.volume_20d_avg <= 0:
            return False                                # insufficient indicator data
        return (
            tech.last > tech.high_20d
            and tech.rsi >= self.rsi_floor
            and tech.macd_hist_last > 0
            and tech.volume_last >= self.volume_multiple * tech.volume_20d_avg
        )

    def expected_holding_days(self) -> tuple[int, int]:
        return (self.holding_days_min, self.holding_days_max)
