from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List

import pandas as pd


# Canonical stress windows. Dates are inclusive [start, end].
HISTORICAL_REGIMES: Dict[str, tuple[str, str]] = {
    "gfc_2008": ("2008-09-01", "2009-03-31"),
    "covid_crash_2020": ("2020-02-19", "2020-03-23"),
    "rates_shock_2022": ("2022-01-03", "2022-10-14"),
    "dotcom_2000": ("2000-03-01", "2002-10-09"),
    "volmageddon_2018": ("2018-02-02", "2018-02-12"),
}


@dataclass
class StressResult:
    regime: str
    start: str
    end: str
    portfolio_return: float          # cumulative
    worst_day: float
    drawdown: float
    notes: str = ""


def stress_test(
    positions: list[dict],
    history: Dict[str, pd.DataFrame],
    regimes: Iterable[str] | None = None,
) -> List[StressResult]:
    """Replay each historical regime against the current portfolio composition.

    `positions`: [{ticker, weight}] — weights sum to gross exposure.
    `history`: ticker -> daily OHLCV DataFrame with DatetimeIndex.
    """
    results: List[StressResult] = []
    targets = list(regimes) if regimes else list(HISTORICAL_REGIMES.keys())
    for name in targets:
        if name not in HISTORICAL_REGIMES:
            continue
        start, end = HISTORICAL_REGIMES[name]
        slice_returns = []
        for p in positions:
            df = history.get(p["ticker"].upper())
            if df is None or df.empty:
                continue
            df = df.copy()
            if not isinstance(df.index, pd.DatetimeIndex):
                df.index = pd.to_datetime(df.index)
            slc = df.loc[start:end]
            if slc.empty:
                continue
            r = slc["Close"].astype(float).pct_change().fillna(0.0) * float(p["weight"])
            slice_returns.append(r)
        if not slice_returns:
            results.append(StressResult(name, start, end, 0.0, 0.0, 0.0, "no data for regime"))
            continue
        port = pd.concat(slice_returns, axis=1).fillna(0.0).sum(axis=1)
        cum = (1 + port).cumprod()
        worst = float(port.min())
        dd = float((cum / cum.cummax() - 1).min())
        results.append(StressResult(
            regime=name,
            start=start,
            end=end,
            portfolio_return=float(cum.iloc[-1] - 1),
            worst_day=worst,
            drawdown=dd,
        ))
    return results
