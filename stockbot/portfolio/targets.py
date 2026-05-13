from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from ..config import Config


@dataclass
class TargetStatus:
    target_annual: float
    days_elapsed: int
    expected_equity: float
    actual_equity: float
    actual_return: float          # cumulative since start
    annualized_return: float
    on_track: bool
    delta_vs_expected: float      # dollars

    def headline(self) -> str:
        return (
            f"Target {self.target_annual*100:.0f}%/yr | "
            f"day {self.days_elapsed} | "
            f"return {self.actual_return*100:+.2f}% "
            f"(annualized {self.annualized_return*100:+.2f}%) | "
            f"{'ON TRACK' if self.on_track else 'OFF TRACK'} "
            f"({self.delta_vs_expected:+,.0f} vs expected)"
        )


def _parse_iso(ts: str) -> datetime:
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        dt = datetime.utcnow()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def evaluate(cfg: Config, starting_cash: float, started_at: str, equity: float) -> TargetStatus:
    target = float(cfg.portfolio.get("target_annual_return", 0.40))
    start_dt = _parse_iso(started_at)
    now = datetime.now(timezone.utc)
    days = max(1, (now - start_dt).days)
    # Daily compounding from annual target.
    daily_rate = (1 + target) ** (1 / 365) - 1
    expected = starting_cash * (1 + daily_rate) ** days
    actual_return = (equity / starting_cash) - 1 if starting_cash > 0 else 0.0
    annualized = (1 + actual_return) ** (365 / days) - 1 if equity > 0 else -1.0
    on_track = equity >= expected * 0.95   # 5% slack
    return TargetStatus(
        target_annual=target,
        days_elapsed=days,
        expected_equity=expected,
        actual_equity=equity,
        actual_return=actual_return,
        annualized_return=annualized,
        on_track=on_track,
        delta_vs_expected=equity - expected,
    )
