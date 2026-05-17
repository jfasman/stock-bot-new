from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class DriftReport:
    realized_sharpe: float
    expected_sharpe: float
    realized_vol: float
    expected_vol: float
    z_score: float                # how many stdevs the realized result is below expectation
    drift_flag: bool
    note: str = ""


def vol_drift(
    realized_returns: pd.Series,
    expected_vol: float,
    threshold_ratio: float = 1.5,
) -> tuple[float, bool]:
    """Compare realized annualized vol to expected. Returns (realized_vol, flag)."""
    if realized_returns is None or realized_returns.empty:
        return 0.0, False
    rv = float(realized_returns.std() * (252 ** 0.5))
    return rv, rv > expected_vol * threshold_ratio or rv < expected_vol / threshold_ratio


def performance_drift(
    realized_returns: pd.Series,
    expected_sharpe: float,
    expected_vol: float,
    z_threshold: float = 2.0,
) -> DriftReport:
    """Flag when live performance deviates from backtest distribution by > z_threshold sigma.

    Treat backtest expected Sharpe as the mean and assume sigma = 1/sqrt(N) for
    annualized Sharpe with N years — rough but useful as a tripwire.
    """
    if realized_returns is None or realized_returns.empty:
        return DriftReport(0, expected_sharpe, 0, expected_vol, 0, False, "no live data yet")
    rv, vol_breach = vol_drift(realized_returns, expected_vol)
    mu = float(realized_returns.mean() * 252)
    sr = mu / rv if rv > 0 else 0.0
    n_years = max(0.1, len(realized_returns) / 252)
    sigma_sr = 1.0 / (n_years ** 0.5)
    z = (sr - expected_sharpe) / sigma_sr if sigma_sr > 0 else 0.0
    flag = abs(z) >= z_threshold or vol_breach
    note = []
    if abs(z) >= z_threshold:
        note.append(f"Sharpe z={z:+.2f}")
    if vol_breach:
        note.append("vol regime shift")
    return DriftReport(
        realized_sharpe=sr,
        expected_sharpe=expected_sharpe,
        realized_vol=rv,
        expected_vol=expected_vol,
        z_score=z,
        drift_flag=flag,
        note="; ".join(note),
    )
