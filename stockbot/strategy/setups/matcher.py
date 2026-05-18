"""Pure setup matcher. Takes already-fetched reads, returns the
setups whose pattern fits — and (separately) picks the single best
match for the conviction gate's one-MatchedSetup contract.
"""
from __future__ import annotations

from typing import Optional

from ...data.fundamentals import Fundamentals
from ...data.macro import MacroSnapshot
from ...ops.setup_performance import SetupPerformance
from ..scorer import TechnicalRead
from . import ALL_SETUPS, Setup
from .types import OptionsContext


def match(
    tech: TechnicalRead,
    fundamentals: Optional[Fundamentals],
    options: Optional[OptionsContext],
    macro: MacroSnapshot,
    setups: tuple[Setup, ...] = ALL_SETUPS,
) -> list[Setup]:
    """Return every setup whose pattern fits the inputs.

    Order preserves `setups` registration order so callers without
    performance data have a deterministic priority tiebreak.
    """
    return [s for s in setups if s.matches(tech, fundamentals, options, macro)]


def pick_best(
    matches: list[Setup],
    perf_by_name: dict[str, SetupPerformance],
) -> Optional[Setup]:
    """Pick the single setup the conviction gate should use.

    Priority:
      1. Highest `expectancy` among matches with a performance row.
      2. If none of the matches have a performance row, first by
         registration order (caller's responsibility — `match()`
         preserves it).

    Returns `None` only when `matches` is empty.
    """
    if not matches:
        return None
    with_perf = [(s, perf_by_name[s.name]) for s in matches if s.name in perf_by_name]
    if with_perf:
        return max(with_perf, key=lambda pair: pair[1].expectancy)[0]
    return matches[0]
