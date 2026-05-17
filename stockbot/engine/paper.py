from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, List

from ..config import Config
from ..data import options as opt_data
from ..data import prices as price_data
from ..ops import audit, guardrails, recommendation_log
from ..ops.config_snapshot import hash_config, snapshot as snap_config
from ..portfolio.portfolio import Portfolio, Position
from ..portfolio.risk import SizingDecision, size_equity, size_option
from ..reporting import journal
from ..strategy.ideas import Idea, generate_ideas
from ..strategy.thesis import thesis_from_idea

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
    elif idea.instrument == "etf":
        # ETFs size like equity but the dollar weight is throttled by leverage
        # so notional exposure stays inside the configured cap.
        lev = max(1.0, abs(idea.leverage_factor))
        sizing = size_equity(cfg, equity, idea.last_price, "long")
        if lev > 1.0 and sizing.quantity > 0:
            scaled = max(1, int(sizing.quantity / lev))
            sizing = type(sizing)(
                quantity=scaled,
                cost=scaled * idea.last_price,
                stop_price=sizing.stop_price,
                target_price=sizing.target_price,
                reason=f"{sizing.reason}; scaled by 1/{lev:.0f} for {lev:.0f}x leverage",
            )
    else:
        if idea.option_mid is None or idea.option_mid <= 0:
            return None
        sizing = size_option(cfg, equity, idea.option_mid)

    if sizing.quantity <= 0 or sizing.cost > available:
        return None

    # Build the Thesis from the legacy Idea and log it BEFORE attempting to open.
    # Every recommendation is recorded, executed or not — that's the audit trail.
    weight = sizing.cost / equity if equity > 0 else 0.0
    thesis = thesis_from_idea(idea, suggested_weight=weight, sizing_rationale=sizing.reason)
    thesis.stop_price = sizing.stop_price
    thesis.target_price = sizing.target_price
    thesis.max_loss_dollars = (
        (idea.last_price - sizing.stop_price) * sizing.quantity
        if idea.instrument == "equity" and sizing.stop_price else None
    )
    thesis.invalidation_triggers = [
        f"stop {sizing.stop_price}" if sizing.stop_price else "no stop set",
        f"score falls below {float(cfg.strategy.get('min_score_to_trade', 0.55)):+.2f}",
    ]
    thesis.config_hash = hash_config(cfg)
    rec_id = recommendation_log.log_thesis(thesis)

    # Behavioral guardrails: concentration, leverage, loss cooldown.
    held_value = sum(
        (price_data.get_last_price(p.ticker) or p.entry_price) * p.quantity * (100 if p.instrument in ("call", "put") else 1)
        for p in portfolio.list_open()
    )
    concentration = (held_value / equity) if equity > 0 else 0.0
    decision = guardrails.check_pre_trade(
        proposed_weight=weight,
        portfolio_concentration=concentration,
    )
    if not decision.allow:
        audit.log_event("guardrail.block", {"ticker": idea.ticker, "reasons": decision.reasons})
        log.info("Guardrail blocked %s: %s", idea.ticker, "; ".join(decision.reasons))
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
                notes=f"score {idea.score:+.2f}; rec #{rec_id}; " + "; ".join(idea.reasons[:3]),
            )
        elif idea.instrument == "etf":
            pid = portfolio.open_position(
                ticker=idea.ticker,
                instrument="etf",
                direction="long",
                quantity=sizing.quantity,
                entry_price=idea.last_price,
                stop_price=sizing.stop_price,
                target_price=sizing.target_price,
                notes=(
                    f"score {idea.score:+.2f}; rec #{rec_id}; "
                    f"{idea.leverage_factor:+.0f}x"
                    + (f" via {idea.alternative_to}; " if idea.alternative_to else "; ")
                    + "; ".join(idea.reasons[:3])
                ),
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
                notes=f"score {idea.score:+.2f}; rec #{rec_id}; " + "; ".join(idea.reasons[:3]),
            )
        recommendation_log.mark_executed(rec_id, pid)
        journal.record_entry(pid, entry_rationale="; ".join(idea.reasons[:5]) or thesis.headline(), tags="systematic")
        audit.log_event("position.opened", {"position_id": pid, "rec_id": rec_id, "ticker": idea.ticker})
        return f"#{pid} {idea.headline()} qty {sizing.quantity:.0f}"
    except ValueError as exc:
        log.info("Skip open %s: %s", idea.ticker, exc)
        return None


def step(cfg: Config, portfolio: Portfolio, tickers: Iterable[str]) -> StepResult:
    """Run one cycle: evaluate exits, then scan for new entries."""
    snap_config(cfg)  # ensure config is snapshotted for every cycle
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

    # Engage loss cooldown if today's drop exceeds the configured threshold.
    curve = portfolio.equity_curve()
    notes: List[str] = []
    if len(curve) >= 2:
        prev_equity = curve[-2][1]
        if prev_equity > 0:
            day_pct = (equity_after - prev_equity) / prev_equity
            threshold = -float(cfg.risk.get("max_daily_drawdown_pct", 0.04))
            if day_pct <= threshold:
                guardrails.trigger_loss_cooldown(loss_pct=day_pct, cooldown_hours=24)
                notes.append(f"cooldown engaged: day return {day_pct:.2%} <= {threshold:.2%}")

    return StepResult(opened=opened, closed=closed, equity=equity_after, notes=notes)
