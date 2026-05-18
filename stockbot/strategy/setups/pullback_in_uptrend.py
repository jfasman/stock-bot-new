"""Buying the dip in an established uptrend — price retraces toward
SMA20 from above while still inside the trend.

Roadmap §13.2:
  SMA20 > SMA50, price within 0.5 ATR of SMA20 from above,
  RSI between 40 and 55.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..scorer import TechnicalRead
from ...data.fundamentals import Fundamentals
from ...data.macro import MacroSnapshot
from .types import OptionsContext


@dataclass(frozen=True)
class PullbackInUptrend:
    name: str = "pullback_in_uptrend"
    direction: str = "long"
    instrument_hint: str = "equity"
    atr_proximity: float = 0.5
    rsi_min: float = 40.0
    rsi_max: float = 55.0
    holding_days_min: int = 5
    holding_days_max: int = 20

    def matches(
        self,
        tech: TechnicalRead,
        fundamentals: Optional[Fundamentals],
        options: Optional[OptionsContext],
        macro: MacroSnapshot,
    ) -> bool:
        if tech.atr14 <= 0 or tech.sma20 <= 0 or tech.sma50 <= 0:
            return False
        uptrend = tech.sma20 > tech.sma50
        above_sma20 = tech.last >= tech.sma20
        near_sma20 = (tech.last - tech.sma20) <= self.atr_proximity * tech.atr14
        rsi_in_band = self.rsi_min <= tech.rsi <= self.rsi_max
        return uptrend and above_sma20 and near_sma20 and rsi_in_band

    def expected_holding_days(self) -> tuple[int, int]:
        return (self.holding_days_min, self.holding_days_max)
