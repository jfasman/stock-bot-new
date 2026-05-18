"""Tests for ops/live_order_audit.py — the Phase B audit + status machine.

Phase A doesn't drive the state machine itself; this test surface exists so
Phase B's routing change has a tested foundation to plug into.
"""
from __future__ import annotations

from stockbot.ops import live_order_audit as loa


def _stage_one(**overrides) -> int:
    kwargs = dict(
        ticker="AAPL", side="buy", quantity=100, backend="alpaca",
    )
    kwargs.update(overrides)
    return loa.stage(**kwargs)


def test_stage_returns_id_and_records_staged_status():
    oid = _stage_one()
    row = loa.get(oid)
    assert row["status"] == "staged"
    assert row["ticker"] == "AAPL"
    assert row["side"] == "buy"
    assert row["quantity"] == 100
    assert row["backend"] == "alpaca"


def test_happy_path_staged_to_filled():
    oid = _stage_one()
    assert loa.approve(oid)
    assert loa.get(oid)["status"] == "approved"
    assert loa.mark_submitted(oid, broker_order_id="bx-1")
    assert loa.get(oid)["status"] == "submitted"
    assert loa.get(oid)["broker_order_id"] == "bx-1"
    assert loa.mark_filled(oid, fill_price=175.0, fill_quantity=100)
    final = loa.get(oid)
    assert final["status"] == "filled"
    assert final["fill_price"] == 175.0


def test_reject_branch_from_staged():
    oid = _stage_one()
    assert loa.reject(oid)
    assert loa.get(oid)["status"] == "rejected"


def test_can_only_approve_from_staged():
    """A staged-rejected order can't be approved retroactively."""
    oid = _stage_one()
    loa.reject(oid)
    assert loa.approve(oid) is False  # already terminal


def test_can_only_submit_from_approved():
    oid = _stage_one()
    # No approve → submit should refuse.
    assert loa.mark_submitted(oid, broker_order_id="bx-2") is False


def test_can_only_fill_from_submitted():
    oid = _stage_one()
    loa.approve(oid)
    # Skipping submit; fill should refuse.
    assert loa.mark_filled(oid, fill_price=1.0, fill_quantity=1.0) is False


def test_error_works_from_any_non_terminal_state():
    """An order can error out from staged, approved, or submitted."""
    for prep in (
        lambda i: None,  # stays in staged
        lambda i: loa.approve(i),
        lambda i: (loa.approve(i), loa.mark_submitted(i, broker_order_id=f"b{i}")),
    ):
        oid = _stage_one()
        prep(oid)
        assert loa.mark_errored(oid, message="vendor 503")
        assert loa.get(oid)["status"] == "errored"


def test_error_refused_from_terminal_state():
    oid = _stage_one()
    loa.reject(oid)
    assert loa.mark_errored(oid, message="too late") is False


def test_staged_returns_only_staged_rows():
    a = _stage_one()
    b = _stage_one(ticker="MSFT")
    c = _stage_one(ticker="NVDA")
    loa.approve(b)
    loa.reject(c)
    staged_ids = {r["id"] for r in loa.staged()}
    assert a in staged_ids
    assert b not in staged_ids
    assert c not in staged_ids


def test_recent_returns_all_statuses_newest_first():
    a = _stage_one()
    b = _stage_one()
    rows = loa.recent(limit=10)
    ids = [r["id"] for r in rows]
    assert ids.index(b) < ids.index(a)  # b inserted last → first
