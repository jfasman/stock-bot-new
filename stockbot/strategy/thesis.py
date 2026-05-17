from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional


@dataclass
class SignalReading:
    name: str
    value: float
    note: str = ""


@dataclass
class Thesis:
    """Structured recommendation. Replaces the looser `Idea` for outputs that
    need to defend themselves to the user before any capital is risked.

    A recommendation without these fields is just an opinion.
    """
    ticker: str
    instrument: str                          # 'equity' | 'call' | 'put' | 'vertical' | 'iron_condor' | ...
    direction: str                           # 'long' | 'short'
    score: float                             # composite [-1, 1]

    # 1. Signal sources and their current readings.
    signals: List[SignalReading] = field(default_factory=list)

    # 2. Expected return and uncertainty band.
    expected_return: Optional[float] = None       # decimal, e.g. 0.12 for 12%
    return_stdev: Optional[float] = None          # std of return distribution
    horizon_days: int = 21

    # 3. Recommended position size with justification.
    suggested_weight: float = 0.0
    sizing_rationale: str = ""

    # 4. Risk scenarios and stop / re-evaluation triggers.
    stop_price: Optional[float] = None
    target_price: Optional[float] = None
    max_loss_dollars: Optional[float] = None

    # 5. What would invalidate the thesis.
    invalidation_triggers: List[str] = field(default_factory=list)

    # Optional context.
    last_price: Optional[float] = None
    option_symbol: Optional[str] = None
    option_strike: Optional[float] = None
    option_expiration: Optional[str] = None
    option_mid: Optional[float] = None

    # Audit fields.
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    config_hash: Optional[str] = None
    extras: Dict[str, str] = field(default_factory=dict)

    def headline(self) -> str:
        side = "LONG" if self.direction == "long" else "SHORT"
        if self.instrument == "equity":
            return f"{self.ticker} {side} (score {self.score:+.2f})"
        strike = f"{self.option_strike:.1f}" if self.option_strike else "?"
        exp = self.option_expiration or "?"
        return f"{self.ticker} {self.instrument.upper()} {strike} {exp} (score {self.score:+.2f})"

    def expected_band(self) -> Optional[tuple[float, float]]:
        if self.expected_return is None or self.return_stdev is None:
            return None
        return (self.expected_return - self.return_stdev, self.expected_return + self.return_stdev)

    def falsifiable_summary(self) -> str:
        """One-liner the bot can show before any trade is taken."""
        if not self.invalidation_triggers:
            return "no falsification criteria defined"
        return "INVALIDATE IF: " + "; ".join(self.invalidation_triggers)


def thesis_from_idea(idea, suggested_weight: float = 0.0, sizing_rationale: str = "") -> Thesis:
    """Bridge from legacy Idea objects so existing code paths keep working
    while callers migrate to producing Thesis directly.
    """
    signals = [SignalReading(name="rsi", value=getattr(idea, "rsi", 0.0))]
    if getattr(idea, "sentiment_net", 0.0):
        signals.append(SignalReading(name="sentiment_net", value=idea.sentiment_net))
    if getattr(idea, "sentiment_confidence", 0.0):
        signals.append(SignalReading(name="sentiment_confidence", value=idea.sentiment_confidence))
    return Thesis(
        ticker=idea.ticker,
        instrument=idea.instrument,
        direction=idea.direction,
        score=idea.score,
        signals=signals,
        last_price=getattr(idea, "last_price", None),
        option_symbol=getattr(idea, "option_symbol", None),
        option_strike=getattr(idea, "option_strike", None),
        option_expiration=getattr(idea, "option_expiration", None),
        option_mid=getattr(idea, "option_mid", None),
        suggested_weight=suggested_weight,
        sizing_rationale=sizing_rationale,
        invalidation_triggers=list(getattr(idea, "reasons", []))[:0],  # legacy reasons aren't falsification rules
        extras={"reasons": "; ".join(getattr(idea, "reasons", []))},
    )
