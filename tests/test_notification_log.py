"""Tests for the notification audit + state machine. Spec: roadmap §13.3."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from stockbot.ops.notification_log import (
    ack,
    active_snooze,
    count_ticker_last_week,
    count_today,
    last_dispatch_at,
    record,
    recent,
    snooze,
)
from stockbot.ops.notify import NotificationPayload
from stockbot.strategy.conviction import ConvictionPick, TimeToAct
from stockbot.strategy.ideas import Idea


def _payload(ticker: str = "AAPL", score: float = 0.82) -> NotificationPayload:
    return NotificationPayload(
        ticker=ticker, direction="long", instrument="equity",
        score=score, matched_setup=None, setup_expectancy=None,
        time_to_act="open_tomorrow", confidence_band=(score - 0.1, score),
    )


def _pick(ticker: str = "AAPL", score: float = 0.82) -> ConvictionPick:
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


def test_record_persists_and_returns_row_id():
    row_id = record(_payload(), _pick(), {"stdout": True, "file": True})
    assert row_id > 0
    rows = recent()
    assert len(rows) == 1
    row = rows[0]
    assert row["ticker"] == "AAPL"
    assert row["delivered_ok"] == 1
    assert json.loads(row["backend_results_json"]) == {"stdout": True, "file": True}


def test_record_delivered_ok_zero_when_no_backend_succeeded():
    record(_payload(), _pick(), {"stdout": False, "file": False})
    assert recent()[0]["delivered_ok"] == 0


def test_ack_sets_acked_at_and_returns_true():
    rid = record(_payload(), _pick(), {"stdout": True})
    assert ack(rid) is True
    row = recent()[0]
    assert row["acked_at"] is not None


def test_ack_returns_false_for_unknown_id():
    assert ack(99999) is False


def test_snooze_sets_snoozed_until_and_returns_true():
    rid = record(_payload(), _pick(), {"stdout": True})
    assert snooze(rid, hours=4) is True
    row = recent()[0]
    assert row["snoozed_until"] is not None


def test_active_snooze_finds_unexpired_window():
    rid = record(_payload("AAPL", score=0.80), _pick("AAPL", 0.80), {"stdout": True})
    snooze(rid, hours=4)
    s = active_snooze("AAPL")
    assert s is not None
    assert s["id"] == rid
    assert s["score"] == 0.80
    # Case-insensitive lookup.
    assert active_snooze("aapl") is not None


def test_active_snooze_ignores_expired():
    rid = record(_payload("AAPL"), _pick("AAPL"), {"stdout": True})
    # Snooze 1h in the past.
    past = datetime.utcnow() - timedelta(hours=1)
    snooze(rid, hours=0.5, ts=past - timedelta(hours=0.5))
    assert active_snooze("AAPL") is None


def test_last_dispatch_at_returns_most_recent_successful():
    record(_payload("AAPL"), _pick("AAPL"), {"stdout": False})   # failed dispatch ignored
    record(_payload("AAPL"), _pick("AAPL"), {"stdout": True})
    ts = last_dispatch_at("AAPL")
    assert ts is not None
    assert last_dispatch_at("UNKNOWN") is None


def test_count_today_counts_only_today_successes():
    now = datetime(2026, 5, 18, 12, 0)
    yesterday = now - timedelta(days=1)
    record(_payload("AAPL"), _pick("AAPL"), {"stdout": True}, ts=yesterday)
    record(_payload("MSFT"), _pick("MSFT"), {"stdout": True}, ts=now)
    record(_payload("GOOG"), _pick("GOOG"), {"stdout": False}, ts=now)  # failed
    assert count_today(now) == 1                              # only MSFT today + ok


def test_count_ticker_last_week_per_ticker_window():
    now = datetime(2026, 5, 18, 12, 0)
    record(_payload("AAPL"), _pick("AAPL"), {"stdout": True}, ts=now - timedelta(days=10))  # too old
    record(_payload("AAPL"), _pick("AAPL"), {"stdout": True}, ts=now - timedelta(days=2))
    record(_payload("AAPL"), _pick("AAPL"), {"stdout": True}, ts=now - timedelta(hours=1))
    record(_payload("MSFT"), _pick("MSFT"), {"stdout": True}, ts=now - timedelta(hours=1))
    assert count_ticker_last_week("AAPL", now) == 2
    assert count_ticker_last_week("MSFT", now) == 1
    assert count_ticker_last_week("NVDA", now) == 0
