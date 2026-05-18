"""Watch loop — the cadence-driven step+gate+notify orchestrator.
Spec: roadmap §13.3.

Pure functional core (`should_dispatch`, `in_market_hours`) so the
rate-limit and snooze logic is testable without sleeping or
spawning a paper engine. The `run_watch_loop` shell just drives
those primitives on a configurable cadence.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

from ..config import Config
from ..data import macro as macro_data
from ..data import prices as price_data
from ..data.fundamentals import get_fundamentals
from ..ops import notification_log
from ..ops.config_snapshot import hash_config, snapshot as snap_config
from ..ops.conviction_log import log_evaluation
from ..ops.notify import (
    NotificationPayload,
    Notifier,
    build_notifiers,
    dispatch,
    payload_from_pick,
)
from ..portfolio.portfolio import Portfolio
from ..strategy.conviction import ConvictionPick, evaluate
from ..strategy.conviction_context import build_context
from ..strategy.ideas import Idea, generate_ideas
from ..strategy.scorer import read_technicals

log = logging.getLogger(__name__)

_NYSE_OPEN = (9, 30)
_NYSE_CLOSE = (16, 0)
_NYSE_TZ = ZoneInfo("America/New_York")


# ── pure helpers (testable without network/clock) ────────────────────────
def in_market_hours(now: Optional[datetime] = None) -> bool:
    """True iff `now` (default: actual now) falls inside the NYSE 09:30-16:00 ET
    window on a weekday. Holiday-aware market calendars are out of scope.
    """
    when = now or datetime.now(tz=timezone.utc)
    et = when.astimezone(_NYSE_TZ) if when.tzinfo else when.replace(tzinfo=timezone.utc).astimezone(_NYSE_TZ)
    if et.weekday() >= 5:                                    # Sat/Sun
        return False
    open_minutes = _NYSE_OPEN[0] * 60 + _NYSE_OPEN[1]
    close_minutes = _NYSE_CLOSE[0] * 60 + _NYSE_CLOSE[1]
    now_minutes = et.hour * 60 + et.minute
    return open_minutes <= now_minutes < close_minutes


@dataclass(frozen=True)
class DispatchDecision:
    """Outcome of the should_dispatch policy check. `reason` is
    populated whether the answer is yes or no so the audit trail
    explains the decision either way.
    """
    allowed: bool
    reason: str


def should_dispatch(
    pick: ConvictionPick,
    cfg: Config,
    now: Optional[datetime] = None,
) -> DispatchDecision:
    """Policy gate between conviction-PASS and actually firing a
    notification. Returns (allowed, reason) so the watch loop can log
    the suppression.

    Rules, in order (first failure wins):
      1. Day-cap: total successful dispatches today ≥ max_per_day.
      2. Ticker-cap: dispatches for this ticker in last 7d ≥
         max_per_ticker_per_week.
      3. Snooze: ticker has an unexpired snooze AND the current
         score hasn't moved more than `resurface_score_delta` from
         the snoozed score.
    """
    section = cfg.raw.get("notifications", {})
    max_per_day = int(section.get("max_per_day", 5))
    max_per_ticker = int(section.get("max_per_ticker_per_week", 2))
    resurface_delta = float(section.get("resurface_score_delta", 0.10))

    if notification_log.count_today(now) >= max_per_day:
        return DispatchDecision(False, f"day cap ({max_per_day}) reached")

    if notification_log.count_ticker_last_week(pick.idea.ticker, now) >= max_per_ticker:
        return DispatchDecision(
            False, f"per-ticker week cap ({max_per_ticker}) reached for {pick.idea.ticker}"
        )

    snooze = notification_log.active_snooze(pick.idea.ticker, now)
    if snooze is not None:
        score_move = abs(pick.idea.score - snooze["score"])
        if score_move < resurface_delta:
            return DispatchDecision(
                False,
                f"snoozed until {snooze['snoozed_until']:%Y-%m-%d %H:%M} "
                f"(score moved {score_move:+.3f}, needs ≥ {resurface_delta:+.3f})",
            )

    return DispatchDecision(True, "policy ok")


# ── single-pass orchestrator ─────────────────────────────────────────────
@dataclass
class WatchCycleResult:
    cycle_started_at: datetime
    n_ideas: int
    n_passes: int
    n_dispatched: int
    n_suppressed: int
    suppressions: list[str]


def run_single_pass(
    cfg: Config,
    portfolio: Portfolio,
    tickers: Iterable[str],
    notifiers: Optional[list[Notifier]] = None,
    now: Optional[datetime] = None,
) -> WatchCycleResult:
    """One pass: generate ideas → gate → log → dispatch (if policy OK).

    Mirrors `paper convict` for the gate+log part, then layers the
    notification dispatch on top. Caller decides whether to call this
    in a loop (see `run_watch_loop`) or one-shot.
    """
    started = now or datetime.utcnow()
    notifiers = notifiers if notifiers is not None else build_notifiers(cfg)

    ideas = list(generate_ideas(cfg, list(tickers)))
    if not ideas:
        return WatchCycleResult(started, 0, 0, 0, 0, [])

    snap_config(cfg)
    cfg_hash = hash_config(cfg)
    macro = macro_data.snapshot()

    tech_cache: dict[str, object] = {}
    fund_cache: dict[str, object] = {}

    n_pass = 0
    n_dispatched = 0
    suppressions: list[str] = []

    for idea in ideas:
        if idea.ticker not in tech_cache:
            df = price_data.get_history(idea.ticker, period="6mo", interval="1d")
            tech_cache[idea.ticker] = read_technicals(df, cfg)
            fund_cache[idea.ticker] = get_fundamentals(idea.ticker)
        ctx = build_context(
            cfg, idea, macro=macro,
            tech=tech_cache[idea.ticker],
            fundamentals=fund_cache[idea.ticker],
            now=started,
        )
        pick, verdicts = evaluate(idea, ctx)
        log_evaluation(idea, verdicts, pick, ts=started, config_hash=cfg_hash)
        if pick is None:
            continue
        n_pass += 1
        decision = should_dispatch(pick, cfg, now=started)
        if not decision.allowed:
            suppressions.append(f"{idea.ticker}: {decision.reason}")
            continue
        payload = payload_from_pick(pick)
        results = dispatch(notifiers, payload)
        notification_log.record(payload, pick, results, ts=started)
        if any(results.values()):
            n_dispatched += 1
        else:
            suppressions.append(f"{idea.ticker}: every backend failed")

    return WatchCycleResult(
        cycle_started_at=started,
        n_ideas=len(ideas),
        n_passes=n_pass,
        n_dispatched=n_dispatched,
        n_suppressed=n_pass - n_dispatched,
        suppressions=suppressions,
    )


# ── continuous loop ──────────────────────────────────────────────────────
def run_watch_loop(
    cfg: Config,
    portfolio: Portfolio,
    tickers: Iterable[str],
    max_cycles: Optional[int] = None,
    sleep_fn=time.sleep,
) -> None:
    """Forever-loop wrapper around `run_single_pass`. Stops on
    KeyboardInterrupt or after `max_cycles` (test hook).

    Honors `notifications.market_hours_only` — when set and outside
    NYSE hours, the loop logs and skips to the next tick rather than
    dispatching.
    """
    section = cfg.raw.get("notifications", {})
    cadence_minutes = float(section.get("cadence_minutes", 15))
    market_hours_only = bool(section.get("market_hours_only", True))
    notifiers = build_notifiers(cfg)
    log.info("watch loop starting; cadence=%.1f min, backends=%s",
             cadence_minutes, [n.name for n in notifiers])

    tickers = list(tickers)
    cycles = 0
    try:
        while True:
            if max_cycles is not None and cycles >= max_cycles:
                break
            if market_hours_only and not in_market_hours():
                log.info("outside market hours; sleeping %.1f min", cadence_minutes)
            else:
                result = run_single_pass(cfg, portfolio, tickers, notifiers=notifiers)
                log.info(
                    "cycle done: ideas=%d passes=%d dispatched=%d suppressed=%d",
                    result.n_ideas, result.n_passes, result.n_dispatched, result.n_suppressed,
                )
                for s in result.suppressions:
                    log.info("  suppressed: %s", s)
            cycles += 1
            sleep_fn(cadence_minutes * 60)
    except KeyboardInterrupt:
        log.info("watch loop interrupted by user; exiting cleanly")
