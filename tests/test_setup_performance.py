"""Tests for the setup_performance store. Spec: roadmap §13.2."""
from __future__ import annotations

from datetime import datetime

from stockbot.ops.setup_performance import (
    SetupPerformance,
    all_performance,
    get_performance,
    upsert_performance,
)


def _perf(
    name: str = "breakout_with_momentum",
    n: int = 40,
    expectancy: float = 0.25,
    ts: datetime | None = None,
) -> SetupPerformance:
    return SetupPerformance(
        setup_name=name, direction="long", n_trades=n,
        win_rate=0.55, avg_r=0.45, expectancy=expectancy, sharpe=1.2,
        last_validated_at=ts or datetime(2026, 5, 17, 12, 0),
    )


def test_upsert_then_get_round_trips_fields():
    perf = _perf(expectancy=0.31)
    upsert_performance(perf)
    fetched = get_performance("breakout_with_momentum")
    assert fetched == perf


def test_get_returns_none_for_unknown_setup():
    assert get_performance("nonexistent_setup") is None


def test_upsert_replaces_existing_row_for_same_name():
    upsert_performance(_perf(n=20, expectancy=0.10, ts=datetime(2026, 1, 1)))
    upsert_performance(_perf(n=40, expectancy=0.25, ts=datetime(2026, 5, 1)))
    fetched = get_performance("breakout_with_momentum")
    assert fetched is not None
    assert fetched.n_trades == 40
    assert fetched.expectancy == 0.25
    assert fetched.last_validated_at == datetime(2026, 5, 1)
    # Only one row total — confirm it overwrote rather than appending.
    assert len(all_performance()) == 1


def test_all_performance_orders_by_expectancy_desc():
    upsert_performance(_perf(name="a", expectancy=0.10))
    upsert_performance(_perf(name="b", expectancy=0.30))
    upsert_performance(_perf(name="c", expectancy=0.20))
    names = [p.setup_name for p in all_performance()]
    assert names == ["b", "c", "a"]
