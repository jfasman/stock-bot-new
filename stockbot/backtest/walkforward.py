from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Callable, Dict, List

import pandas as pd

from .engine import BacktestResult, Backtester, SignalFn
from .metrics import Metrics


@dataclass
class WalkForwardWindow:
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    metrics: Metrics


def _date_range(start: str, end: str, step_days: int):
    cur = datetime.fromisoformat(start).date()
    last = datetime.fromisoformat(end).date()
    while cur <= last:
        yield cur
        cur = cur + timedelta(days=step_days)


def walk_forward(
    price_history: Dict[str, pd.DataFrame],
    signal_builder: Callable[[str, str], SignalFn],
    start: str,
    end: str,
    train_years: float = 2.0,
    test_months: float = 6.0,
    starting_cash: float = 100_000.0,
) -> List[WalkForwardWindow]:
    """Walk-forward harness: train signal on [t-train_years, t], test on
    (t, t+test_months], step by test_months, never re-using test data for training.

    `signal_builder(train_start, train_end)` returns the SignalFn fit on that window.
    """
    bt = Backtester(price_history, starting_cash=starting_cash)
    windows: list[WalkForwardWindow] = []
    train_days = int(train_years * 365)
    step_days = int(test_months * 30)

    for test_start in _date_range(start, end, step_days):
        test_end = test_start + timedelta(days=step_days)
        train_end_d = test_start - timedelta(days=1)
        train_start_d = train_end_d - timedelta(days=train_days)
        signal_fn = signal_builder(str(train_start_d), str(train_end_d))
        result: BacktestResult = bt.run(signal_fn, str(test_start), str(test_end))
        windows.append(WalkForwardWindow(
            train_start=str(train_start_d),
            train_end=str(train_end_d),
            test_start=str(test_start),
            test_end=str(test_end),
            metrics=result.metrics,
        ))
        if test_end >= datetime.fromisoformat(end).date():
            break
    return windows
