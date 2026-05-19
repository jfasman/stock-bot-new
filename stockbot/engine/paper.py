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


# ----------------------------------------------------------------------
# Book-level rebalance (roadmap §13.8)
# ----------------------------------------------------------------------

def _annualized_vol(history) -> float:
    """Annualized realized vol from a daily-close DataFrame. Returns 0 when
    history is too short, so the caller falls back to the mean-vol default."""
    if history is None or history.empty or "Close" not in history.columns:
        return 0.0
    import numpy as np
    rets = history["Close"].astype(float).pct_change().dropna()
    if len(rets) < 20:
        return 0.0
    return float(rets.std() * np.sqrt(252))


def run_rebalance(
    cfg: Config,
    portfolio: Portfolio,
    tickers: Iterable[str],
) -> int | None:
    """Generate a book-level rebalance proposal and record it.

    Returns the proposal row id, or None when the rebalancer is disabled
    or there is nothing to rebalance over. Persisted proposals are
    ``status='pending'`` until approved or rejected.
    """
    rebalance_cfg = cfg.raw.get("rebalance", {}) if hasattr(cfg, "raw") else {}
    if not rebalance_cfg.get("enabled", False):
        log.info("Rebalancer disabled in config — skipping.")
        return None

    from ..data.fundamentals import get_fundamentals
    from ..ops import rebalance as ops_rebalance
    from ..portfolio.constraints import ConstraintConfig
    from ..portfolio.rebalancer import (
        BookPosition,
        CandidatePick,
        RebalanceConfig,
        rebalance as run_rebalance_math,
    )

    # Current book → BookPosition list. Marks come from the same place exits use.
    open_positions = portfolio.list_open()
    marks: dict[str, float] = {}
    for pos in open_positions:
        m = _mark_price(pos)
        if m is not None:
            marks[pos.option_symbol or pos.ticker] = m
    equity = portfolio.equity(marks) or float(cfg.portfolio.get("starting_cash", 100_000))

    book: List[BookPosition] = []
    sectors_cache: dict[str, str | None] = {}
    betas_cache: dict[str, float | None] = {}
    for pos in open_positions:
        if pos.instrument != "equity":
            # Book-level rebalance operates on equity sleeves; options retain
            # whatever sizing the per-position layer chose.
            continue
        mark = marks.get(pos.option_symbol or pos.ticker) or pos.entry_price
        weight = (pos.market_value(mark) / equity) if equity > 0 else 0.0
        try:
            f = get_fundamentals(pos.ticker)
            sectors_cache[pos.ticker] = f.sector
            betas_cache[pos.ticker] = f.beta
        except Exception:
            pass
        book.append(BookPosition(
            ticker=pos.ticker,
            weight=weight,
            sector=sectors_cache.get(pos.ticker),
            beta=betas_cache.get(pos.ticker),
        ))

    # Candidate picks from the live scorer. We only consider long-equity ideas
    # for the rebalancer — options sizing stays per-position.
    ideas = generate_ideas(cfg, tickers)
    picks: List[CandidatePick] = []
    seen: set[str] = set()
    for idea in ideas:
        if idea.instrument != "equity" or idea.direction != "long":
            continue
        if idea.ticker in seen:
            continue
        seen.add(idea.ticker)
        try:
            f = get_fundamentals(idea.ticker)
            sectors_cache.setdefault(idea.ticker, f.sector)
            betas_cache.setdefault(idea.ticker, f.beta)
        except Exception:
            pass
        picks.append(CandidatePick(
            ticker=idea.ticker,
            target_weight=0.0,  # algo computes the actual weight
            sector=sectors_cache.get(idea.ticker),
            beta=betas_cache.get(idea.ticker),
        ))

    if not book and not picks:
        log.info("Rebalancer: nothing in book and no new picks — skipping.")
        return None

    # Realized vols for the inverse-vol kernel.
    vols: dict[str, float] = {}
    for t in {p.ticker for p in book} | {p.ticker for p in picks}:
        try:
            hist = price_data.get_history(t, period="1y", interval="1d")
            v = _annualized_vol(hist)
            if v > 0:
                vols[t] = v
        except Exception as exc:
            log.info("Vol unavailable for %s: %s", t, exc)

    rebalance_config = RebalanceConfig(
        algo=str(rebalance_cfg.get("algo", "equal_risk_contribution")),
        target_vol=float(rebalance_cfg.get("target_vol", 0.15)),
        max_turnover_per_rebalance=float(rebalance_cfg.get("max_turnover_per_rebalance", 0.20)),
    )

    # Build constraints from the existing risk_advanced block.
    risk_adv = cfg.raw.get("risk_advanced", {}) if hasattr(cfg, "raw") else {}
    beta_range = risk_adv.get("target_beta_range", [-0.20, 1.20])
    constraints = ConstraintConfig(
        max_position_pct=float(cfg.portfolio.get("max_position_pct", 0.08)),
        max_sector_pct=float(risk_adv.get("max_sector_pct", 0.30)),
        max_gross_exposure=float(risk_adv.get("max_gross_exposure", 1.50)),
        max_net_exposure=float(risk_adv.get("max_net_exposure", 1.00)),
        target_beta_range=(float(beta_range[0]), float(beta_range[1])),
    )

    proposal = run_rebalance_math(
        book=book, picks=picks, cfg=rebalance_config,
        vols=vols, constraints=constraints,
    )
    config_hash = hash_config(cfg)
    proposal_id = ops_rebalance.record(proposal, config_hash=config_hash)
    audit.log_event(
        "rebalance.proposed",
        {
            "proposal_id": proposal_id,
            "algo": rebalance_config.algo,
            "grows": [c.ticker for c in proposal.grows],
            "shrinks": [c.ticker for c in proposal.shrinks],
            "raw_turnover": proposal.raw_turnover,
            "capped_turnover": proposal.capped_turnover,
            "feasible": proposal.feasible,
        },
    )
    return proposal_id


# ----------------------------------------------------------------------
# Execute an approved rebalance proposal (roadmap §13.8)
# ----------------------------------------------------------------------

@dataclass
class RebalanceExecution:
    """What `execute_rebalance` actually did. Surfaces for the CLI/dashboard."""
    proposal_id: int
    opened: List[str]
    closed: List[str]
    gate_filtered: List[str]              # grows the conviction gate rejected
    skipped: List[str]                    # partials/holds not yet supported
    notes: List[str]


def execute_rebalance(
    cfg: Config,
    portfolio: Portfolio,
    proposal_id: int,
    tickers: Iterable[str],
) -> Optional[RebalanceExecution]:
    """Translate an approved rebalance proposal into paper opens / closes.

    Roadmap §13.8 rule: grows must clear the conviction gate at current
    prices ("the gate is the bar for committing more capital, the rebalancer
    is the bar for how much"). Shrinks skip the gate — exits are always
    allowed.

    First-cut scope: full-open and full-close are supported; partial grows /
    shrinks log as "not yet supported" and the proposal still flips to
    executed. The audit captures every decision so a later iteration can add
    partial sizing without losing the trail.

    Returns None when the proposal is missing or not in `approved` status.
    """
    from ..data import macro as macro_data
    from ..data import prices as price_data
    from ..data.fundamentals import get_fundamentals
    from ..ops import rebalance as ops_rebalance
    from ..strategy.conviction import evaluate as gate_evaluate
    from ..strategy.conviction_context import build_context
    from ..strategy.ideas import generate_ideas
    from ..strategy.scorer import read_technicals

    proposal = ops_rebalance.get(proposal_id)
    if proposal is None:
        log.warning("execute_rebalance: proposal #%d not found", proposal_id)
        return None
    if proposal["status"] != "approved":
        log.warning(
            "execute_rebalance: proposal #%d status is '%s', not 'approved' — refusing.",
            proposal_id, proposal["status"],
        )
        return None

    opened: List[str] = []
    closed: List[str] = []
    gate_filtered: List[str] = []
    skipped: List[str] = []
    notes: List[str] = []

    # Index current positions by ticker for the close path.
    open_by_ticker: Dict[str, List] = {}
    for pos in portfolio.list_open():
        if pos.instrument != "equity":
            continue
        open_by_ticker.setdefault(pos.ticker.upper(), []).append(pos)

    # We'll regenerate ideas + gate context lazily, only for tickers that
    # need a gate check (i.e. opens). Closing doesn't need any of this.
    ideas_by_ticker: Dict[str, Idea] = {}
    macro_snap = None
    tech_cache: Dict[str, object] = {}
    fund_cache: Dict[str, object] = {}

    def _ensure_gate_inputs():
        nonlocal macro_snap
        if not ideas_by_ticker:
            for idea in generate_ideas(cfg, tickers):
                # Only equity-long ideas can satisfy a rebalancer grow.
                if idea.instrument == "equity" and idea.direction == "long":
                    ideas_by_ticker[idea.ticker.upper()] = idea
        if macro_snap is None:
            try:
                macro_snap = macro_data.snapshot()
            except Exception as exc:
                log.info("Macro unavailable; gate may fail closed: %s", exc)

    # Walk the proposed changes.
    eps = 1e-6
    for ch in proposal.get("changes", []):
        ticker = ch["ticker"].upper()
        cur_w = ch["current_weight"]
        tgt_w = ch["target_weight"]
        is_close = abs(tgt_w) < eps and abs(cur_w) >= eps
        is_open = abs(cur_w) < eps and abs(tgt_w) >= eps

        if is_close:
            positions = open_by_ticker.get(ticker, [])
            if not positions:
                notes.append(f"{ticker}: marked close but no open position found.")
                continue
            for pos in positions:
                px = _mark_price(pos)
                if px is None or px <= 0:
                    notes.append(f"{ticker}: no mark price; close skipped for #{pos.id}.")
                    continue
                portfolio.close_position(pos.id, px, notes=f"rebalance #{proposal_id}")
                closed.append(f"{ticker} #{pos.id} @ {px:.2f}")
            audit.log_event("rebalance.executed_close", {
                "proposal_id": proposal_id, "ticker": ticker,
                "position_ids": [p.id for p in positions],
            })
            continue

        if is_open:
            _ensure_gate_inputs()
            idea = ideas_by_ticker.get(ticker)
            if idea is None:
                gate_filtered.append(ticker)
                notes.append(
                    f"{ticker}: no qualifying long-equity idea at current prices "
                    f"(score below threshold or insufficient history)."
                )
                audit.log_event("rebalance.gate_filtered", {
                    "proposal_id": proposal_id, "ticker": ticker,
                    "reason": "no_idea",
                })
                continue

            # Build gate context + run conviction gate.
            if ticker not in tech_cache:
                df = price_data.get_history(ticker, period="6mo", interval="1d")
                tech_cache[ticker] = read_technicals(df, cfg)
                try:
                    fund_cache[ticker] = get_fundamentals(ticker)
                except Exception:
                    fund_cache[ticker] = None
            try:
                gate_ctx = build_context(
                    cfg, idea, macro=macro_snap,
                    tech=tech_cache[ticker], fundamentals=fund_cache[ticker],
                )
                pick, verdicts = gate_evaluate(idea, gate_ctx)
            except Exception as exc:
                log.warning("Gate evaluation failed for %s: %s", ticker, exc)
                pick = None
                verdicts = {}

            if pick is None:
                gate_filtered.append(ticker)
                failing = [name for name, v in verdicts.items() if not v.passed]
                notes.append(f"{ticker}: gate blocked grow — failing: {failing}")
                audit.log_event("rebalance.gate_filtered", {
                    "proposal_id": proposal_id, "ticker": ticker,
                    "failing_gates": failing,
                })
                continue

            # Translate target_weight into shares against current equity.
            marks_snap: Dict[str, float] = {}
            for pos in portfolio.list_open():
                m = _mark_price(pos)
                if m is not None:
                    marks_snap[pos.option_symbol or pos.ticker] = m
            equity_now = portfolio.equity(marks_snap) or float(
                cfg.portfolio.get("starting_cash", 100_000)
            )
            dollars = tgt_w * equity_now
            qty = int(dollars / idea.last_price) if idea.last_price > 0 else 0
            if qty <= 0:
                notes.append(
                    f"{ticker}: target_weight {tgt_w:.2%} on equity ${equity_now:,.0f} "
                    f"→ 0 shares — skipped."
                )
                continue

            cash_reserve = float(cfg.portfolio.get("cash_reserve_pct", 0.10))
            available = portfolio.cash - equity_now * cash_reserve
            cost = qty * idea.last_price
            if cost > available:
                # Trim to fit.
                qty = int(available / idea.last_price)
                if qty <= 0:
                    notes.append(
                        f"{ticker}: insufficient cash after reserve — skipped."
                    )
                    continue

            pid = portfolio.open_position(
                ticker=ticker, instrument="equity", direction="long",
                quantity=qty, entry_price=idea.last_price,
                notes=f"rebalance #{proposal_id} target_w={tgt_w:.2%}",
            )
            opened.append(f"{ticker} #{pid} qty {qty} @ {idea.last_price:.2f}")
            audit.log_event("rebalance.executed_open", {
                "proposal_id": proposal_id, "ticker": ticker,
                "qty": qty, "price": idea.last_price,
                "target_weight": tgt_w, "position_id": pid,
            })
            continue

        # Anything else (grow/shrink/hold partials) — first-cut deferral.
        if abs(tgt_w - cur_w) > eps:
            skipped.append(ticker)
            notes.append(
                f"{ticker}: partial sizing ({cur_w:.2%} → {tgt_w:.2%}) "
                f"not yet supported by executor — proposal noted, no order."
            )

    # Flip the proposal even if some grows were filtered — the *intent* was executed.
    ops_rebalance.mark_executed(proposal_id)
    audit.log_event("rebalance.executed", {
        "proposal_id": proposal_id,
        "opened": opened, "closed": closed,
        "gate_filtered": gate_filtered, "skipped": skipped,
    })
    return RebalanceExecution(
        proposal_id=proposal_id,
        opened=opened, closed=closed,
        gate_filtered=gate_filtered, skipped=skipped,
        notes=notes,
    )
