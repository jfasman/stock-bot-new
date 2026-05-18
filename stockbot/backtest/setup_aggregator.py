"""Aggregate per-trade backtest output into per-setup walk-forward stats.

Spec: roadmap §13.2. Writes one `SetupPerformance` row per
`setup_name` observed in closed trades. The conviction gate's
`setup_validated` check consumes these rows.

Definitions (documented so reviewers can audit the math):
  - r per trade = pnl / (entry_price × quantity)
    A return fraction, *not* a true R-multiple (we don't track
    per-trade initial risk). Same denominator across long/short.
  - win_rate = fraction of trades with pnl > 0
  - avg_r = mean of r across trades
  - expectancy = win_rate × mean(r | r > 0) + (1 - win_rate) × mean(r | r ≤ 0)
    (i.e. expected r per trade, decomposed; equals avg_r by linearity
    but reported separately so a low-expectancy setup with a few
    outlier winners is visible.)
  - sharpe = mean(r) / std(r) × sqrt(trades_per_year)
    trades_per_year estimated from the span between first and last
    closed trade. Returns 0.0 when fewer than 2 trades or std=0.
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Iterable

from ..ops.setup_performance import SetupPerformance, upsert_performance
from .engine import Trade


def aggregate_setup_performance(
    trades: Iterable[Trade],
    as_of: datetime,
) -> list[SetupPerformance]:
    """Compute one SetupPerformance row per setup_name found in
    closed trades. Trades without `setup_name` are ignored.

    Returns the list of rows; caller is responsible for persisting
    (use `persist()` for the common upsert path).
    """
    by_setup: dict[str, list[Trade]] = {}
    for t in trades:
        if not t.closed or t.setup_name is None:
            continue
        by_setup.setdefault(t.setup_name, []).append(t)

    out: list[SetupPerformance] = []
    for name, group in by_setup.items():
        out.append(_perf_for(name, group, as_of))
    return out


def persist(rows: list[SetupPerformance]) -> int:
    """Upsert every row. Returns count persisted."""
    for r in rows:
        upsert_performance(r)
    return len(rows)


def _perf_for(name: str, trades: list[Trade], as_of: datetime) -> SetupPerformance:
    returns = [t.return_pct for t in trades]
    n = len(returns)
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r <= 0]
    win_rate = len(wins) / n if n else 0.0
    avg_r = sum(returns) / n if n else 0.0
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    expectancy = win_rate * avg_win + (1 - win_rate) * avg_loss
    sharpe = _trade_sharpe(returns, trades)
    direction = trades[0].direction                            # consistent within a setup
    return SetupPerformance(
        setup_name=name,
        direction=direction,
        n_trades=n,
        win_rate=win_rate,
        avg_r=avg_r,
        expectancy=expectancy,
        sharpe=sharpe,
        last_validated_at=as_of,
    )


def _trade_sharpe(returns: list[float], trades: list[Trade]) -> float:
    """Annualized Sharpe of trade-return stream. Returns 0.0 when
    too few trades or zero variance — honest 'not enough data' over
    a fragile point estimate.
    """
    n = len(returns)
    if n < 2:
        return 0.0
    mean = sum(returns) / n
    variance = sum((r - mean) ** 2 for r in returns) / (n - 1)
    if variance <= 0:
        return 0.0
    std = math.sqrt(variance)
    # Estimate trades-per-year from the entry-date span.
    try:
        first = datetime.fromisoformat(min(t.entry_date for t in trades))
        last = datetime.fromisoformat(max(t.entry_date for t in trades))
        days = max((last - first).days, 1)
        trades_per_year = n * 365 / days
    except (ValueError, TypeError):
        trades_per_year = float(n)                             # fallback: treat span as one year
    return (mean / std) * math.sqrt(trades_per_year)
