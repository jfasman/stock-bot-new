from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, List

from ..config import Config
from ..data import options as opt_data
from ..data import prices as price_data
from ..portfolio.portfolio import Portfolio, Position
from ..portfolio.risk import SizingDecision, size_equity, size_option
from ..strategy.ideas import Idea, generate_ideas

log = logging.getLogger(__name__)


@dataclass
class StepResult:
    opened: List[str]
    closed: List[str]
    equity: float
    notes: List[str]


def _mark_price(pos: Position) -> float | None:
    if pos.instrument == "equity":
        return price_data.get_last_price(pos.ticker)
    # Options — re-pull the chain to find the same contract by symbol.
    try:
        calls, puts = opt_data.get_chain(pos.ticker, pos.option_expiration or "")
        pool = calls if pos.instrument == "call" else puts
        for c in pool:
            if c.symbol == pos.option_symbol:
                return c.mid
    except Exception:
        return None
    return None


def _evaluate_exits(portfolio: Portfolio) -> tuple[List[str], dict[str, float]]:
    closed: List[str] = []
    marks: dict[str, float] = {}
    for pos in portfolio.list_open():
        mark = _mark_price(pos)
        if mark is None:
            continue
        marks[pos.option_symbol or pos.ticker] = mark

        hit_stop = False
        hit_target = False
        if pos.direction == "long":
            if pos.stop_price is not None and mark <= pos.stop_price:
                hit_stop = True
            if pos.target_price is not None and mark >= pos.target_price:
                hit_target = True
        else:  # short
            if pos.stop_price is not None and mark >= pos.stop_price:
                hit_stop = True
            if pos.target_price is not None and mark <= pos.target_price:
                hit_target = True

        if hit_stop or hit_target:
            reason = "stop hit" if hit_stop else "target hit"
            realized = portfolio.close_position(pos.id, mark, notes=reason)
            closed.append(f"#{pos.id} {pos.ticker} {pos.instrument} {reason} pnl {realized:+.2f}")
    return closed, marks


def _try_open(cfg: Config, portfolio: Portfolio, idea: Idea, equity: float) -> str | None:
    max_open = int(cfg.portfolio.get("max_open_positions", 12))
    if len(portfolio.list_open()) >= max_open:
        return None
    cash_reserve = float(cfg.portfolio.get("cash_reserve_pct", 0.10))
    available = portfolio.cash - equity * cash_reserve
    if available <= 0:
        return None

    if idea.instrument == "equity":
        sizing: SizingDecision = size_equity(cfg, equity, idea.last_price, idea.direction)
    else:
        if idea.option_mid is None or idea.option_mid <= 0:
            return None
        sizing = size_option(cfg, equity, idea.option_mid)

    if sizing.quantity <= 0 or sizing.cost > available:
        return None

    try:
        if idea.instrument == "equity":
            pid = portfolio.open_position(
                ticker=idea.ticker,
                instrument="equity",
                direction=idea.direction,
                quantity=sizing.quantity,
                entry_price=idea.last_price,
                stop_price=sizing.stop_price,
                target_price=sizing.target_price,
                notes=f"score {idea.score:+.2f}; " + "; ".join(idea.reasons[:3]),
            )
        else:
            pid = portfolio.open_position(
                ticker=idea.ticker,
                instrument=idea.instrument,
                direction="long",  # we always go long the option (calls or puts)
                quantity=sizing.quantity,
                entry_price=idea.option_mid or 0.0,
                stop_price=sizing.stop_price,
                target_price=sizing.target_price,
                option_symbol=idea.option_symbol,
                option_strike=idea.option_strike,
                option_expiration=idea.option_expiration,
                notes=f"score {idea.score:+.2f}; " + "; ".join(idea.reasons[:3]),
            )
        return f"#{pid} {idea.headline()} qty {sizing.quantity:.0f}"
    except ValueError as exc:
        log.info("Skip open %s: %s", idea.ticker, exc)
        return None


def step(cfg: Config, portfolio: Portfolio, tickers: Iterable[str]) -> StepResult:
    """Run one cycle: evaluate exits, then scan for new entries."""
    closed, marks = _evaluate_exits(portfolio)

    # Mark equity after closes.
    for pos in portfolio.list_open():
        key = pos.option_symbol or pos.ticker
        if key not in marks:
            mark = _mark_price(pos)
            if mark is not None:
                marks[key] = mark
    equity_now = portfolio.equity(marks)

    ideas = generate_ideas(cfg, tickers)
    opened: List[str] = []
    held = {p.ticker for p in portfolio.list_open()}
    for idea in ideas:
        if idea.ticker in held:
            continue
        result = _try_open(cfg, portfolio, idea, equity_now)
        if result:
            opened.append(result)
            held.add(idea.ticker)

    # Recompute equity & record curve.
    for pos in portfolio.list_open():
        key = pos.option_symbol or pos.ticker
        if key not in marks:
            mark = _mark_price(pos)
            if mark is not None:
                marks[key] = mark
    equity_after = portfolio.equity(marks)
    portfolio.record_equity(equity_after)

    return StepResult(opened=opened, closed=closed, equity=equity_after, notes=[])
