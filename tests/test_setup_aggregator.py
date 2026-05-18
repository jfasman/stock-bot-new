"""Tests for the setup_performance aggregator + Backtester match_fn
hook. Spec: roadmap §13.2 "Validation"."""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from stockbot.backtest import Backtester
from stockbot.backtest.engine import Trade
from stockbot.backtest.setup_aggregator import aggregate_setup_performance, persist
from stockbot.ops.setup_performance import all_performance, get_performance


def _trade(setup_name: str, pnl: float, entry: float = 100.0, qty: float = 10.0,
           entry_date: str = "2024-01-02", exit_date: str = "2024-01-10") -> Trade:
    return Trade(
        ticker="X", entry_date=entry_date, entry_price=entry,
        exit_date=exit_date, exit_price=entry + pnl / qty,
        quantity=qty, direction="long", pnl=pnl, fees=0.0,
        setup_name=setup_name,
    )


# ── aggregator math ──────────────────────────────────────────────────────
def test_aggregator_returns_empty_when_no_setup_trades():
    trades = [_trade(setup_name=None, pnl=10)]  # type: ignore[arg-type]
    assert aggregate_setup_performance(trades, datetime(2026, 5, 17)) == []


def test_aggregator_groups_by_setup_name():
    trades = [
        _trade("breakout_with_momentum", pnl=50),
        _trade("breakout_with_momentum", pnl=-20),
        _trade("pullback_in_uptrend", pnl=30),
    ]
    rows = aggregate_setup_performance(trades, datetime(2026, 5, 17))
    by_name = {r.setup_name: r for r in rows}
    assert set(by_name) == {"breakout_with_momentum", "pullback_in_uptrend"}
    assert by_name["breakout_with_momentum"].n_trades == 2
    assert by_name["pullback_in_uptrend"].n_trades == 1


def test_aggregator_win_rate_and_avg_r():
    # 3 trades on a $1000 notional: +100 (r=+0.10), +50 (r=+0.05), -100 (r=-0.10).
    trades = [
        _trade("s", pnl=100),
        _trade("s", pnl=50),
        _trade("s", pnl=-100),
    ]
    [row] = aggregate_setup_performance(trades, datetime(2026, 5, 17))
    assert row.n_trades == 3
    assert row.win_rate == pytest_approx(2 / 3)
    # avg_r = (0.10 + 0.05 - 0.10) / 3
    assert row.avg_r == pytest_approx((0.10 + 0.05 - 0.10) / 3)
    # expectancy = (2/3) × mean(0.10, 0.05) + (1/3) × (-0.10) = (2/3)×0.075 + (1/3)×(-0.10)
    assert row.expectancy == pytest_approx((2 / 3) * 0.075 + (1 / 3) * -0.10)


def test_aggregator_zero_sharpe_when_too_few_trades_or_zero_variance():
    # 1 trade → sharpe 0.
    [single] = aggregate_setup_performance([_trade("s", pnl=100)], datetime(2026, 5, 17))
    assert single.sharpe == 0.0
    # Identical returns → variance 0 → sharpe 0 (honest "not enough data").
    flat = [_trade("s", pnl=50), _trade("s", pnl=50)]
    [row] = aggregate_setup_performance(flat, datetime(2026, 5, 17))
    assert row.sharpe == 0.0


def test_aggregator_skips_open_trades():
    open_trade = Trade(
        ticker="X", entry_date="2024-01-02", entry_price=100,
        exit_date=None, exit_price=None, quantity=10, direction="long",
        pnl=0, fees=0, setup_name="s",
    )
    closed = _trade("s", pnl=100)
    rows = aggregate_setup_performance([open_trade, closed], datetime(2026, 5, 17))
    assert len(rows) == 1
    assert rows[0].n_trades == 1                               # open trade excluded


def test_persist_writes_rows_to_store():
    trades = [
        _trade("breakout_with_momentum", pnl=80),
        _trade("breakout_with_momentum", pnl=-30),
    ]
    rows = aggregate_setup_performance(trades, datetime(2026, 5, 17))
    persist(rows)
    fetched = get_performance("breakout_with_momentum")
    assert fetched is not None
    assert fetched.n_trades == 2
    assert len(all_performance()) == 1


# ── Backtester match_fn hook ─────────────────────────────────────────────
def _synthetic_history(days: int = 80, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rets = rng.normal(0.001, 0.015, days)
    close = 100 * np.exp(np.cumsum(rets))
    idx = pd.date_range("2024-01-02", periods=days, freq="B")
    return pd.DataFrame({"Close": close, "Volume": rng.integers(1_000_000, 5_000_000, days)}, index=idx)


def test_backtester_attaches_setup_name_via_match_fn():
    history = {"AAA": _synthetic_history()}

    seen_calls: list[str] = []
    def match_fn(ticker, sliced):
        seen_calls.append(ticker)
        return "test_setup"

    bt = Backtester(history, starting_cash=10_000, match_fn=match_fn, rebalance_freq=5)
    result = bt.run(lambda asof, sliced: {"AAA": 0.1}, start="2024-01-02", end="2024-04-15")

    assert len(seen_calls) >= 1                                # match_fn invoked at entry
    assert any(t.setup_name == "test_setup" for t in result.trades)


def test_backtester_without_match_fn_leaves_setup_name_none():
    history = {"AAA": _synthetic_history()}
    bt = Backtester(history, starting_cash=10_000, rebalance_freq=5)
    result = bt.run(lambda asof, sliced: {"AAA": 0.1}, start="2024-01-02", end="2024-04-15")
    assert all(t.setup_name is None for t in result.trades)


def test_backtester_swallows_match_fn_exceptions():
    history = {"AAA": _synthetic_history()}
    def bad_match_fn(ticker, sliced):
        raise RuntimeError("boom")
    bt = Backtester(history, starting_cash=10_000, match_fn=bad_match_fn, rebalance_freq=5)
    # Must not crash the run.
    result = bt.run(lambda asof, sliced: {"AAA": 0.1}, start="2024-01-02", end="2024-04-15")
    assert all(t.setup_name is None for t in result.trades)


def pytest_approx(value, tol: float = 1e-9):
    """Lightweight approx helper to avoid importing pytest.approx everywhere."""
    class _Approx:
        def __eq__(self, other):
            return abs(other - value) < tol
        def __repr__(self):
            return f"~{value}"
    return _Approx()
