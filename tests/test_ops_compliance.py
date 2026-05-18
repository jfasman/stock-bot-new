"""Tests for ops/compliance.py — the required pre-check for live orders.

Both branches (PDT, wash sale) and the combined-pass case are covered.
"""
from __future__ import annotations

from datetime import date, timedelta

from stockbot.ops import wash_sale
from stockbot.ops.compliance import pre_check_live_order
from stockbot.ops.pdt import RoundTrip


def test_passes_when_no_rules_trigger():
    result = pre_check_live_order(
        intent="open", ticker="AAPL",
        intraday_close=False, account_equity=100_000,
        recent_round_trips=[], open_lots=[],
    )
    assert result.ok
    assert result.reasons == []


def test_blocks_intraday_close_when_pdt_flags():
    """4 day trades + <$25k equity → PDT flagged → reject the intraday close."""
    today = date(2026, 5, 18)
    dts = [
        RoundTrip(ticker="AAPL", open_date=today - timedelta(days=i),
                  close_date=today - timedelta(days=i))
        for i in range(1, 5)
    ]
    result = pre_check_live_order(
        intent="close", ticker="AAPL",
        intraday_close=True, account_equity=10_000,   # below 25k
        recent_round_trips=dts, open_lots=[], today=today,
    )
    assert not result.ok
    assert any("PDT" in r for r in result.reasons)
    assert result.pdt_status is not None
    assert result.pdt_status.flagged_as_pdt


def test_pdt_not_run_when_not_intraday_close():
    """A swing trade doesn't trigger PDT — pdt_status should be None."""
    result = pre_check_live_order(
        intent="close", ticker="AAPL",
        intraday_close=False, account_equity=10_000,
        recent_round_trips=[], open_lots=[],
    )
    assert result.ok
    assert result.pdt_status is None


def test_blocks_wash_sale_loss():
    """Selling at a loss when a replacement lot was acquired within 30 days
    → disallowed loss → reject."""
    sold_date = date(2026, 5, 15)
    # Original lot: bought 60d ago at 100.
    matched_lot = wash_sale.Lot(
        ticker="AAPL", quantity=100,
        cost_basis=100.0, acquired=sold_date - timedelta(days=60),
    )
    # Replacement: bought 5 days before the sale at 90.
    replacement = wash_sale.Lot(
        ticker="AAPL", quantity=100,
        cost_basis=90.0, acquired=sold_date - timedelta(days=5),
    )
    sale = wash_sale.Sale(
        ticker="AAPL", quantity=100, proceeds_per_share=80.0,
        sold=sold_date, matched_lot=matched_lot,
    )
    result = pre_check_live_order(
        intent="close", ticker="AAPL",
        intraday_close=False, account_equity=100_000,
        recent_round_trips=[],
        open_lots=[matched_lot, replacement],
        proposed_sale=sale,
    )
    assert not result.ok
    assert any("Wash-sale" in r for r in result.reasons)
    assert len(result.wash_sale_events) == 1


def test_wash_sale_not_triggered_on_gain():
    """Selling at a gain doesn't trigger the wash-sale rule (only losses)."""
    sold_date = date(2026, 5, 15)
    matched = wash_sale.Lot(
        ticker="AAPL", quantity=100, cost_basis=100.0,
        acquired=sold_date - timedelta(days=60),
    )
    repl = wash_sale.Lot(
        ticker="AAPL", quantity=100, cost_basis=90.0,
        acquired=sold_date - timedelta(days=5),
    )
    sale_at_gain = wash_sale.Sale(
        ticker="AAPL", quantity=100, proceeds_per_share=120.0,   # > basis
        sold=sold_date, matched_lot=matched,
    )
    result = pre_check_live_order(
        intent="close", ticker="AAPL",
        intraday_close=False, account_equity=100_000,
        recent_round_trips=[], open_lots=[matched, repl],
        proposed_sale=sale_at_gain,
    )
    assert result.ok
    assert result.wash_sale_events == []


def test_multiple_failures_aggregate_into_reasons():
    """Both PDT and wash sale can fail in the same call — both surface."""
    today = date(2026, 5, 18)
    dts = [
        RoundTrip(ticker="AAPL", open_date=today - timedelta(days=i),
                  close_date=today - timedelta(days=i))
        for i in range(1, 5)
    ]
    matched = wash_sale.Lot(ticker="AAPL", quantity=100, cost_basis=100.0,
                            acquired=today - timedelta(days=60))
    repl = wash_sale.Lot(ticker="AAPL", quantity=100, cost_basis=90.0,
                         acquired=today - timedelta(days=5))
    sale = wash_sale.Sale(ticker="AAPL", quantity=100, proceeds_per_share=80.0,
                          sold=today, matched_lot=matched)
    result = pre_check_live_order(
        intent="close", ticker="AAPL",
        intraday_close=True, account_equity=10_000,
        recent_round_trips=dts,
        open_lots=[matched, repl], proposed_sale=sale,
        today=today,
    )
    assert not result.ok
    assert len(result.reasons) >= 2
