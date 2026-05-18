import numpy as np
import pandas as pd

from stockbot.backtest import Backtester, CostModel, compute_metrics


def _synthetic(tickers, days=500, seed=0):
    rng = np.random.default_rng(seed)
    out = {}
    idx = pd.date_range("2022-01-03", periods=days, freq="B")
    for i, t in enumerate(tickers):
        drift = 0.0008 + 0.0002 * i
        rets = rng.normal(drift, 0.012, days)
        out[t] = pd.DataFrame(
            {"Close": 100 * np.exp(np.cumsum(rets)), "Volume": rng.integers(1e6, 1e7, days)},
            index=idx,
        )
    return out


def test_backtester_equal_weight_signal_runs_and_makes_money():
    tickers = ["A", "B", "C"]
    hist = _synthetic(tickers, days=400, seed=1)
    bt = Backtester(hist, starting_cash=100_000.0, rebalance_freq=20)

    def signal(asof, sliced):
        return {t: 1.0 / len(tickers) for t in tickers}

    result = bt.run(signal, start="2022-02-01", end="2023-06-30")
    assert not result.equity_curve.empty
    assert result.metrics.trades > 0


def test_backtester_book_rebalancer_hook_transforms_signal():
    """The book_rebalancer hook gets a chance to rewrite signal weights
    before the engine applies them. Roadmap §13.8."""
    tickers = ["A", "B", "C"]
    hist = _synthetic(tickers, days=300, seed=2)

    calls: list[tuple[dict, dict]] = []

    def passthrough_rebalancer(current_weights: dict, signal_weights: dict) -> dict:
        calls.append((current_weights, signal_weights))
        # Halve every signal weight; rest stays cash.
        return {t: w * 0.5 for t, w in signal_weights.items()}

    bt = Backtester(
        hist, starting_cash=100_000.0, rebalance_freq=10,
        book_rebalancer=passthrough_rebalancer,
    )

    def signal(asof, sliced):
        return {t: 1.0 / len(tickers) for t in tickers}

    result = bt.run(signal, start="2022-02-01", end="2022-12-30")
    assert len(calls) > 0  # hook fired at least once
    # First call should see empty current_weights (no positions yet).
    assert calls[0][0] == {}
    # Each call's signal_weights should match what signal() returned.
    for _, sw in calls:
        assert all(abs(w - 1 / 3) < 1e-9 for w in sw.values())
    assert not result.equity_curve.empty


def test_backtester_no_signal_holds_cash():
    tickers = ["A"]
    hist = _synthetic(tickers, days=300)
    bt = Backtester(hist, starting_cash=100_000.0, rebalance_freq=10)

    def signal(asof, sliced):
        return {}

    result = bt.run(signal, start="2022-02-01", end="2022-12-30")
    # No trades placed, equity stays at starting cash.
    assert all(abs(v - 100_000.0) < 1e-6 for v in result.equity_curve.values)


def test_cost_model_charges_commission_on_options():
    cm = CostModel()
    cost = cm.option_cost(contracts=5, mid=2.50)
    # 5 contracts × $0.65 commission = $3.25 + spread
    assert cost > 3.25


def test_compute_metrics_handles_empty():
    m = compute_metrics(pd.Series(dtype=float))
    assert m.sharpe == 0
    assert m.max_drawdown == 0


def test_backtester_no_cash_leak_on_final_liquidation():
    # Flat-price universe, zero-cost model: holding through should preserve equity exactly.
    idx = pd.date_range("2022-01-03", periods=200, freq="B")
    hist = {"FLAT": pd.DataFrame({"Close": [100.0] * 200, "Volume": [1_000_000] * 200}, index=idx)}
    zero_cost = CostModel(
        commission_per_share=0.0,
        half_spread_bps=0.0,
        impact_coefficient=0.0,
        fee_finra_per_share=0.0,
    )
    bt = Backtester(hist, starting_cash=100_000.0, rebalance_freq=20, cost_model=zero_cost)

    def signal(asof, sliced):
        return {"FLAT": 0.10}

    result = bt.run(signal, start="2022-02-01", end="2022-10-30")
    final_eq = float(result.equity_curve.iloc[-1])
    # With zero costs and flat prices, equity must equal starting cash within float tolerance.
    assert abs(final_eq - 100_000.0) < 1e-6, f"cash leaked on final liquidation: {final_eq}"


def test_backtester_equity_continuity_on_final_bar():
    # The last equity point must not cliff vs the second-to-last. Catches the
    # cash-overwrite bug where final liquidation replaced (not added to) cash.
    tickers = ["A", "B", "C"]
    hist = _synthetic(tickers, days=300, seed=7)
    bt = Backtester(hist, starting_cash=100_000.0, rebalance_freq=20)

    def signal(asof, sliced):
        return {t: 1.0 / len(tickers) for t in tickers}

    result = bt.run(signal, start="2022-02-01", end="2023-01-30")
    eq = result.equity_curve
    pct_change_last = abs(eq.iloc[-1] / eq.iloc[-2] - 1.0)
    assert pct_change_last < 0.02, f"final bar cliff {pct_change_last:.1%}: cash accounting bug"


def test_backtester_rotation_preserves_cash():
    # Strategy that fully rotates every rebalance — this exercises close-during-rebalance.
    # With zero costs and flat prices, equity must remain at starting cash.
    idx = pd.date_range("2022-01-03", periods=200, freq="B")
    hist = {
        "X": pd.DataFrame({"Close": [100.0] * 200, "Volume": [1_000_000] * 200}, index=idx),
        "Y": pd.DataFrame({"Close": [50.0] * 200, "Volume": [1_000_000] * 200}, index=idx),
    }
    zero_cost = CostModel(
        commission_per_share=0.0,
        half_spread_bps=0.0,
        impact_coefficient=0.0,
        fee_finra_per_share=0.0,
    )
    bt = Backtester(hist, starting_cash=100_000.0, rebalance_freq=10, cost_model=zero_cost)

    flip = {"v": 0}

    def signal(asof, sliced):
        flip["v"] = 1 - flip["v"]
        return {"X": 0.10} if flip["v"] else {"Y": 0.10}

    result = bt.run(signal, start="2022-02-01", end="2022-10-30")
    final_eq = float(result.equity_curve.iloc[-1])
    assert abs(final_eq - 100_000.0) < 1e-6, f"rotation leaked cash: {final_eq}"
