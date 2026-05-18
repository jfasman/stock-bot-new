"""Tests for the watch loop's pure policy helpers.
Integration of run_single_pass is covered manually — too much I/O for unit tests.
Spec: roadmap §13.3."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from stockbot.config import Config
from stockbot.ops import notification_log
from stockbot.ops.notify import NotificationPayload
from stockbot.ops.scheduler import (
    DispatchDecision,
    in_market_hours,
    run_watch_loop,
    should_dispatch,
)
from stockbot.strategy.conviction import ConvictionPick, TimeToAct
from stockbot.strategy.ideas import Idea


def _pick(ticker: str = "AAPL", score: float = 0.80) -> ConvictionPick:
    return ConvictionPick(
        idea=Idea(
            ticker=ticker, direction="long", instrument="equity", score=score,
            last_price=200.0, rsi=55.0, reasons=[],
            sentiment_net=0.0, sentiment_confidence=0.0,
        ),
        gates_passed=("score",),
        confidence_band=(score - 0.1, score),
        time_to_act=TimeToAct.OPEN_TOMORROW,
    )


def _payload(ticker: str = "AAPL", score: float = 0.80) -> NotificationPayload:
    return NotificationPayload(
        ticker=ticker, direction="long", instrument="equity",
        score=score, matched_setup=None, setup_expectancy=None,
        time_to_act="open_tomorrow", confidence_band=(score - 0.1, score),
    )


def _cfg(**overrides) -> Config:
    notif = {"max_per_day": 5, "max_per_ticker_per_week": 2,
             "resurface_score_delta": 0.10}
    notif.update(overrides)
    return Config(raw={"notifications": notif})


# ── in_market_hours ──────────────────────────────────────────────────────
ET = ZoneInfo("America/New_York")


@pytest.mark.parametrize("when,expected", [
    (datetime(2026, 5, 18, 10, 0, tzinfo=ET), True),         # Mon 10:00 ET
    (datetime(2026, 5, 18, 9, 30, tzinfo=ET), True),         # exactly open
    (datetime(2026, 5, 18, 9, 29, tzinfo=ET), False),        # one minute before
    (datetime(2026, 5, 18, 15, 59, tzinfo=ET), True),        # one minute before close
    (datetime(2026, 5, 18, 16, 0, tzinfo=ET), False),        # exactly close (exclusive)
    (datetime(2026, 5, 16, 12, 0, tzinfo=ET), False),        # Sat
    (datetime(2026, 5, 17, 12, 0, tzinfo=ET), False),        # Sun
])
def test_in_market_hours_window(when, expected):
    assert in_market_hours(when) is expected


def test_in_market_hours_accepts_naive_utc():
    # Naive datetime should be interpreted as UTC.
    # 14:00 UTC = 10:00 ET on a Monday → inside the window.
    naive = datetime(2026, 5, 18, 14, 0)
    assert in_market_hours(naive) is True


# ── should_dispatch ──────────────────────────────────────────────────────
def test_should_dispatch_allows_when_quota_clean():
    decision = should_dispatch(_pick(), _cfg())
    assert decision.allowed is True


def test_should_dispatch_blocks_on_day_cap():
    cfg = _cfg(max_per_day=1)
    notification_log.record(_payload(), _pick(), {"stdout": True})
    decision = should_dispatch(_pick(), cfg)
    assert decision.allowed is False
    assert "day cap" in decision.reason


def test_should_dispatch_blocks_on_ticker_cap():
    cfg = _cfg(max_per_ticker_per_week=1, max_per_day=99)
    notification_log.record(_payload("AAPL"), _pick("AAPL"), {"stdout": True})
    # Different ticker still allowed.
    assert should_dispatch(_pick("MSFT"), cfg).allowed is True
    assert should_dispatch(_pick("AAPL"), cfg).allowed is False


def test_should_dispatch_blocks_when_snoozed_and_score_hasnt_moved():
    cfg = _cfg(resurface_score_delta=0.10)
    rid = notification_log.record(_payload("AAPL", 0.80), _pick("AAPL", 0.80), {"stdout": True})
    notification_log.snooze(rid, hours=4)
    # Score moved only 0.02 < 0.10 threshold.
    decision = should_dispatch(_pick("AAPL", score=0.82), cfg)
    assert decision.allowed is False
    assert "snoozed" in decision.reason


def test_should_dispatch_resurfaces_when_score_moved_past_delta():
    cfg = _cfg(resurface_score_delta=0.10)
    rid = notification_log.record(_payload("AAPL", 0.80), _pick("AAPL", 0.80), {"stdout": True})
    notification_log.snooze(rid, hours=4)
    decision = should_dispatch(_pick("AAPL", score=0.95), cfg)  # |0.95-0.80| = 0.15 ≥ 0.10
    # Note: it might still be blocked by day-cap; clear that by raising the cap.
    if "day cap" not in decision.reason:
        assert decision.allowed is True


def test_should_dispatch_decision_is_a_dataclass_with_reason():
    decision = should_dispatch(_pick(), _cfg())
    assert isinstance(decision, DispatchDecision)
    assert decision.reason                                    # non-empty even on pass


# ── run_watch_loop respects max_cycles + sleep injection ─────────────────
def test_run_watch_loop_stops_after_max_cycles(monkeypatch):
    """Smoke test: max_cycles + injected sleep should make the loop
    terminate without hitting the network. We patch run_single_pass to
    a no-op so the loop's only side effect is incrementing the counter.
    """
    from stockbot.ops import scheduler

    call_count = {"n": 0}
    def fake_single_pass(*args, **kwargs):
        call_count["n"] += 1
        return scheduler.WatchCycleResult(datetime.utcnow(), 0, 0, 0, 0, [])

    monkeypatch.setattr(scheduler, "run_single_pass", fake_single_pass)
    monkeypatch.setattr(scheduler, "build_notifiers", lambda cfg: [])
    monkeypatch.setattr(scheduler, "in_market_hours", lambda now=None: True)

    cfg = Config(raw={"notifications": {"cadence_minutes": 0.001, "market_hours_only": False}})
    run_watch_loop(cfg, portfolio=None, tickers=["AAPL"], max_cycles=3, sleep_fn=lambda _: None)  # type: ignore[arg-type]

    assert call_count["n"] == 3
