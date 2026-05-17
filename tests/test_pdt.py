from datetime import date, timedelta

from stockbot.ops.pdt import RoundTrip, evaluate


def test_under_25k_flagged_after_four_day_trades():
    today = date(2025, 5, 15)
    rts = [RoundTrip("AAPL", today - timedelta(days=i), today - timedelta(days=i)) for i in range(4)]
    status = evaluate(account_equity=10_000, recent_round_trips=rts, today=today)
    assert status.flagged_as_pdt
    assert not status.can_open_intraday


def test_above_25k_not_flagged():
    today = date(2025, 5, 15)
    rts = [RoundTrip("AAPL", today - timedelta(days=i), today - timedelta(days=i)) for i in range(4)]
    status = evaluate(account_equity=50_000, recent_round_trips=rts, today=today)
    assert not status.flagged_as_pdt


def test_warn_before_threshold():
    today = date(2025, 5, 15)
    rts = [RoundTrip("AAPL", today - timedelta(days=i), today - timedelta(days=i)) for i in range(3)]
    status = evaluate(account_equity=10_000, recent_round_trips=rts, today=today)
    assert "1 more day trade" in status.note
