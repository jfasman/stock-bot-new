"""Setup library — named, hand-defined, hypothesis-driven trade patterns.

Spec: roadmap §13.2. Each setup is a frozen dataclass with class-level
attrs (`name`, `direction`, `instrument_hint`) and two methods —
`matches()` returns whether the inputs fit the pattern;
`expected_holding_days()` returns a `(min, max)` tuple used by the
conviction gate to derive `time_to_act`.

Setups are pure. They consume already-computed reads (TechnicalRead,
Fundamentals, OptionsContext, MacroSnapshot) and return bools. They
do not fetch data, write to the store, or maintain state.

Adding a setup is a one-file change:
  1. Drop `strategy/setups/<name>.py` with a frozen dataclass.
  2. Append the instance to `ALL_SETUPS` below.
  3. Add a `tests/test_setups.py::test_<name>_*` case.

Resist seeding more than ~6. The deflated-Sharpe discipline applies to
setup count as much as to factor count.
"""
from __future__ import annotations

from typing import Literal, Optional, Protocol, runtime_checkable

from ..scorer import TechnicalRead
from ...data.fundamentals import Fundamentals
from ...data.macro import MacroSnapshot
from .types import OptionsContext


@runtime_checkable
class Setup(Protocol):
    name: str
    direction: Literal["long", "short"]
    instrument_hint: Literal["equity", "call", "put", "etf"]

    def matches(
        self,
        tech: TechnicalRead,
        fundamentals: Optional[Fundamentals],
        options: Optional[OptionsContext],
        macro: MacroSnapshot,
    ) -> bool: ...

    def expected_holding_days(self) -> tuple[int, int]: ...


from .breakout_with_momentum import BreakoutWithMomentum       # noqa: E402
from .pullback_in_uptrend import PullbackInUptrend             # noqa: E402
from .mean_reversion_oversold import MeanReversionOversold     # noqa: E402
from .iv_crush_premium_sell import IVCrushPremiumSell          # noqa: E402


# Registry order is also priority order when expectancy data is unavailable;
# see strategy/setups/matcher.py.
ALL_SETUPS: tuple[Setup, ...] = (
    BreakoutWithMomentum(),
    PullbackInUptrend(),
    MeanReversionOversold(),
    IVCrushPremiumSell(),
)
