from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import numpy as np
import pandas as pd

from ..data import options as opt_data
from ..data import prices as price_data
from ..data.greeks import dte_to_years, greeks, implied_vol


@dataclass
class IVProfile:
    ticker: str
    current_iv: Optional[float]
    iv_rank: Optional[float]        # 0..100 — current vs 1y high/low
    iv_percentile: Optional[float]  # 0..100 — fraction of last year below today
    put_call_skew_25d: Optional[float]
    term_structure_slope: Optional[float]  # back-month - front-month IV
    front_iv: Optional[float]
    back_iv: Optional[float]


def _historical_vol(close: pd.Series, window: int = 30) -> Optional[float]:
    if len(close) < window + 1:
        return None
    rets = close.pct_change().dropna().tail(window)
    if rets.empty or rets.std() == 0:
        return None
    return float(rets.std() * (252 ** 0.5))


def _atm_iv(ticker: str, expiration: str, spot: float) -> Optional[float]:
    calls, puts = opt_data.get_chain(ticker, expiration)
    if not calls or spot <= 0:
        return None
    nearest = min(calls, key=lambda c: abs(c.strike - spot))
    if nearest.implied_vol and nearest.implied_vol > 0:
        return float(nearest.implied_vol)
    # Fallback: solve from mid price.
    if nearest.mid > 0:
        t = dte_to_years(nearest.dte)
        return implied_vol(nearest.mid, spot, nearest.strike, t, rate=0.045, option_type="call")
    return None


def iv_rank_percentile(ticker: str, current_iv: float, lookback_days: int = 252) -> tuple[Optional[float], Optional[float]]:
    """Approximate IV rank/percentile using realized vol as a proxy when we lack
    a stored IV time series. Replace with stored IV history once persisted.
    """
    df = price_data.get_history(ticker, period="1y", interval="1d")
    if df.empty:
        return None, None
    close = df["Close"].astype(float)
    rv_series = close.pct_change().rolling(30).std().dropna() * (252 ** 0.5)
    if rv_series.empty:
        return None, None
    hi = float(rv_series.max())
    lo = float(rv_series.min())
    rank = (current_iv - lo) / (hi - lo) * 100 if hi > lo else None
    pct = float((rv_series < current_iv).mean() * 100)
    return rank, pct


def put_call_skew(ticker: str, expiration: str, spot: float) -> Optional[float]:
    """IV(25Δ put) − IV(25Δ call). Positive = put skew (typical for indices)."""
    calls, puts = opt_data.get_chain(ticker, expiration)
    if not calls or not puts or spot <= 0:
        return None
    t = dte_to_years((calls[0].dte if calls else 0))
    if t <= 0:
        return None

    def nearest_delta(contracts, target_delta, opt_type):
        best = None
        best_d = float("inf")
        for c in contracts:
            if c.implied_vol and c.implied_vol > 0:
                iv = c.implied_vol
            elif c.mid > 0:
                iv = implied_vol(c.mid, spot, c.strike, t, rate=0.045, option_type=opt_type)
            else:
                continue
            if not iv:
                continue
            d = greeks(spot, c.strike, t, 0.045, iv, opt_type).delta
            dist = abs(abs(d) - target_delta)
            if dist < best_d:
                best_d = dist
                best = (c, iv)
        return best

    call25 = nearest_delta(calls, 0.25, "call")
    put25 = nearest_delta(puts, 0.25, "put")
    if not call25 or not put25:
        return None
    return float(put25[1] - call25[1])


def term_structure(ticker: str, spot: float) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """Return (front_iv, back_iv, slope). Slope = back - front; positive = contango."""
    exps = opt_data.list_expirations(ticker)
    if len(exps) < 2:
        return None, None, None
    front_iv = _atm_iv(ticker, exps[0], spot)
    # Pick a back month roughly 60+ DTE if available.
    back_exp = None
    for e in exps[1:]:
        from datetime import datetime
        try:
            dte = (datetime.strptime(e, "%Y-%m-%d").date() - datetime.utcnow().date()).days
        except Exception:
            continue
        if dte >= 60:
            back_exp = e
            break
    back_exp = back_exp or exps[-1]
    back_iv = _atm_iv(ticker, back_exp, spot)
    slope = (back_iv - front_iv) if (front_iv and back_iv) else None
    return front_iv, back_iv, slope


def profile(ticker: str) -> IVProfile:
    spot = price_data.get_last_price(ticker) or 0.0
    exps = opt_data.list_expirations(ticker)
    current = _atm_iv(ticker, exps[0], spot) if exps else None
    rank, pct = (None, None)
    if current:
        rank, pct = iv_rank_percentile(ticker, current)
    skew = put_call_skew(ticker, exps[0], spot) if exps else None
    front, back, slope = term_structure(ticker, spot)
    return IVProfile(
        ticker=ticker.upper(),
        current_iv=current,
        iv_rank=rank,
        iv_percentile=pct,
        put_call_skew_25d=skew,
        term_structure_slope=slope,
        front_iv=front,
        back_iv=back,
    )


# ---------- Defined-risk structure analyzers ----------


@dataclass
class StructureAnalysis:
    name: str           # 'vertical', 'iron_condor', 'covered_call', 'cash_secured_put', 'calendar'
    max_gain: float
    max_loss: float
    breakevens: List[float]
    expected_value: Optional[float]
    prob_profit: Optional[float]
    notes: str = ""


def vertical(long_strike: float, short_strike: float, long_premium: float, short_premium: float, opt_type: str) -> StructureAnalysis:
    """Debit or credit vertical spread analyzer.

    For a call debit spread: long_strike < short_strike, long_premium > short_premium.
    """
    width = abs(short_strike - long_strike)
    net_debit = long_premium - short_premium
    if opt_type == "call":
        if long_strike < short_strike:
            # Bull call debit
            max_gain = (width - net_debit) * 100
            max_loss = net_debit * 100
            be = [long_strike + net_debit]
        else:
            max_gain = -net_debit * 100
            max_loss = (width + net_debit) * 100
            be = [long_strike - net_debit]
    else:
        if long_strike > short_strike:
            max_gain = (width - net_debit) * 100
            max_loss = net_debit * 100
            be = [long_strike - net_debit]
        else:
            max_gain = -net_debit * 100
            max_loss = (width + net_debit) * 100
            be = [long_strike + net_debit]
    return StructureAnalysis(
        name="vertical",
        max_gain=float(max_gain),
        max_loss=float(max_loss),
        breakevens=be,
        expected_value=None,
        prob_profit=None,
        notes=f"width {width:.2f}, net {'debit' if net_debit > 0 else 'credit'} {abs(net_debit):.2f}",
    )


def iron_condor(
    call_short: float, call_long: float, put_short: float, put_long: float,
    call_short_prem: float, call_long_prem: float, put_short_prem: float, put_long_prem: float,
) -> StructureAnalysis:
    """Short iron condor: sell call_short, buy call_long (higher strike); sell put_short, buy put_long (lower strike)."""
    credit = (call_short_prem - call_long_prem) + (put_short_prem - put_long_prem)
    call_wing = abs(call_long - call_short)
    put_wing = abs(put_short - put_long)
    width = max(call_wing, put_wing)
    max_gain = credit * 100
    max_loss = (width - credit) * 100
    be_low = put_short - credit
    be_high = call_short + credit
    return StructureAnalysis(
        name="iron_condor",
        max_gain=float(max_gain),
        max_loss=float(max_loss),
        breakevens=[be_low, be_high],
        expected_value=None,
        prob_profit=None,
        notes=f"credit {credit:.2f}, widest wing {width:.2f}",
    )


def covered_call(spot: float, strike: float, premium: float) -> StructureAnalysis:
    max_gain = ((strike - spot) + premium) * 100
    max_loss = (spot - premium) * 100  # if stock goes to 0
    be = [spot - premium]
    return StructureAnalysis(
        name="covered_call",
        max_gain=float(max_gain),
        max_loss=float(max_loss),
        breakevens=be,
        expected_value=None,
        prob_profit=None,
        notes=f"sold {strike} call for {premium:.2f}",
    )


def cash_secured_put(strike: float, premium: float) -> StructureAnalysis:
    max_gain = premium * 100
    max_loss = (strike - premium) * 100  # if underlying goes to 0
    be = [strike - premium]
    return StructureAnalysis(
        name="cash_secured_put",
        max_gain=float(max_gain),
        max_loss=float(max_loss),
        breakevens=be,
        expected_value=None,
        prob_profit=None,
        notes=f"sold {strike} put for {premium:.2f}",
    )


def lognormal_prob_above(spot: float, target: float, t_years: float, sigma: float, rate: float = 0.045) -> float:
    """Probability the underlying ends above `target` under the lognormal model."""
    from math import log, sqrt
    from ..data.greeks import _N  # type: ignore
    if spot <= 0 or target <= 0 or sigma <= 0 or t_years <= 0:
        return 0.0
    d2 = (log(spot / target) + (rate - 0.5 * sigma * sigma) * t_years) / (sigma * sqrt(t_years))
    return _N(d2)


def attach_probabilities(analysis: StructureAnalysis, spot: float, t_years: float, sigma: float) -> StructureAnalysis:
    """Approximate prob_profit and expected_value under the IV-implied lognormal.

    Caveat: real return distributions have fat tails; treat this as a directional sanity check.
    """
    if not analysis.breakevens or t_years <= 0 or sigma <= 0:
        return analysis
    be = sorted(analysis.breakevens)
    if len(be) == 1:
        if "credit" in analysis.notes or analysis.name in ("covered_call", "cash_secured_put"):
            pop = lognormal_prob_above(spot, be[0], t_years, sigma)
        else:
            pop = lognormal_prob_above(spot, be[0], t_years, sigma)
    else:
        below = 1 - lognormal_prob_above(spot, be[0], t_years, sigma)
        above = lognormal_prob_above(spot, be[1], t_years, sigma)
        pop = max(0.0, 1.0 - below - above)
    ev = analysis.max_gain * pop - analysis.max_loss * (1 - pop)
    analysis.prob_profit = float(np.clip(pop, 0.0, 1.0))
    analysis.expected_value = float(ev)
    return analysis
