"""Tests for reporting/live_vs_paper.py — paper vs live book comparison."""
from __future__ import annotations

from datetime import datetime

import pytest

from stockbot.engine.brokers.base import LivePosition
from stockbot.portfolio.portfolio import Position
from stockbot.reporting.live_vs_paper import compare


def _paper(ticker: str, qty: float, entry: float, *, instrument: str = "equity") -> Position:
    return Position(
        id=1, ticker=ticker, instrument=instrument, direction="long",
        quantity=qty, entry_price=entry,
        entry_date=datetime.utcnow().isoformat(),
        stop_price=None, target_price=None,
        option_symbol=None, option_strike=None, option_expiration=None,
        status="open", exit_price=None, exit_date=None,
        realized_pnl=None, notes=None,
    )


def _live(ticker: str, qty: float, entry: float, *, current: float, pnl: float) -> LivePosition:
    return LivePosition(
        ticker=ticker, quantity=qty, avg_entry_price=entry,
        current_price=current, market_value=current * qty,
        unrealized_pnl=pnl, unrealized_pnl_pct=(pnl / (entry * qty) if qty else 0),
    )


def test_empty_books_produce_empty_report():
    r = compare([], [])
    assert r.rows == []
    assert r.both_count == 0


def test_ticker_in_both_books_classified_correctly():
    paper = [_paper("AAPL", qty=100, entry=150.0)]
    live = [_live("AAPL", qty=100, entry=150.0, current=175.0, pnl=2500.0)]
    r = compare(paper, live, paper_marks={"AAPL": 175.0})
    assert r.both_count == 1
    assert r.paper_only_count == 0
    assert r.live_only_count == 0
    row = r.rows[0]
    assert row.status == "both"
    assert row.paper_unrealized_pnl == pytest.approx(2500.0)
    assert row.live_unrealized_pnl == pytest.approx(2500.0)
    assert row.pnl_delta == pytest.approx(0.0)


def test_paper_only_classification():
    paper = [_paper("AAPL", 100, 150.0)]
    r = compare(paper, [], paper_marks={"AAPL": 160.0})
    assert r.rows[0].status == "paper_only"
    assert r.paper_only_count == 1


def test_live_only_classification():
    live = [_live("MSFT", 50, 300.0, current=320.0, pnl=1000.0)]
    r = compare([], live)
    assert r.rows[0].status == "live_only"
    assert r.live_only_count == 1


def test_quantity_delta_sign():
    """Live has 150 shares vs paper's 100 → delta is +50."""
    paper = [_paper("AAPL", 100, 150.0)]
    live = [_live("AAPL", 150, 150.0, current=175.0, pnl=3750.0)]
    r = compare(paper, live, paper_marks={"AAPL": 175.0})
    assert r.rows[0].quantity_delta == 50


def test_options_excluded_from_comparison():
    """Cluster 5 is equity-only; option positions in the paper book
    should be ignored, not break the comparison."""
    paper_eq = _paper("AAPL", 100, 150.0)
    paper_opt = Position(
        id=2, ticker="AAPL", instrument="call", direction="long",
        quantity=1, entry_price=5.0,
        entry_date=datetime.utcnow().isoformat(),
        stop_price=None, target_price=None,
        option_symbol="AAPL260619C00155000",
        option_strike=155.0, option_expiration="2026-06-19",
        status="open", exit_price=None, exit_date=None,
        realized_pnl=None, notes=None,
    )
    live = [_live("AAPL", 100, 150.0, current=175.0, pnl=2500.0)]
    r = compare([paper_eq, paper_opt], live, paper_marks={"AAPL": 175.0})
    # Only one row — the option position didn't add a second entry.
    assert len(r.rows) == 1
    assert r.rows[0].paper_quantity == 100   # the option's qty of 1 was excluded


def test_paper_only_aggregates_multiple_positions_per_ticker():
    """Two paper buys of AAPL → one comparison row, summed."""
    paper = [_paper("AAPL", 50, 150.0), _paper("AAPL", 50, 160.0)]
    r = compare(paper, [], paper_marks={"AAPL": 170.0})
    row = r.rows[0]
    assert row.paper_quantity == 100
    # Weighted-avg entry = (50*150 + 50*160) / 100 = 155
    assert row.paper_avg_entry == pytest.approx(155.0)


def test_live_total_is_none_when_any_pnl_missing():
    """A live position with unrealized_pnl=None poisons the live total — the
    aggregate is intentionally None rather than silently wrong."""
    live_with_pnl = _live("AAPL", 100, 150.0, current=175.0, pnl=2500.0)
    live_missing = LivePosition(
        ticker="MSFT", quantity=50, avg_entry_price=300.0,
        current_price=None, market_value=None,
        unrealized_pnl=None, unrealized_pnl_pct=None,
    )
    r = compare([], [live_with_pnl, live_missing])
    assert r.live_total_unrealized is None


def test_paper_only_and_live_only_appear_in_notes():
    paper = [_paper("AAPL", 100, 150.0)]
    live = [_live("NVDA", 10, 800.0, current=900.0, pnl=1000.0)]
    r = compare(paper, live, paper_marks={"AAPL": 160.0})
    note_text = " ".join(r.notes)
    assert "AAPL" in note_text
    assert "NVDA" in note_text


def test_tickers_sorted_alphabetically():
    paper = [_paper("MSFT", 10, 100), _paper("AAPL", 10, 100)]
    r = compare(paper, [], paper_marks={"MSFT": 100, "AAPL": 100})
    assert [row.ticker for row in r.rows] == ["AAPL", "MSFT"]
