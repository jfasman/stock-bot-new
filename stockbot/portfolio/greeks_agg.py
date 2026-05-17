from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from ..data import options as opt_data
from ..data import prices as price_data
from ..data.greeks import GreekSet, dte_to_years, greeks, implied_vol
from ..strategy import leveraged_etfs as letfs
from .portfolio import Portfolio


@dataclass
class PortfolioGreeks:
    delta: float           # share-equivalent
    gamma: float
    theta: float           # dollars / day
    vega: float            # dollars per 1% IV move
    rho: float             # dollars per 1% rate move
    by_ticker: Dict[str, GreekSet]
    thresholds_breached: List[str]


def aggregate_greeks(
    portfolio: Portfolio,
    risk_free_rate: float = 0.045,
    vega_threshold: float = 5000.0,
    gamma_threshold: float = 500.0,
) -> PortfolioGreeks:
    """Sum portfolio Greeks across all open option positions and report threshold breaches.

    Equity positions contribute delta = quantity * sign, others zero.
    """
    total = GreekSet(price=0, delta=0, gamma=0, theta=0, vega=0, rho=0)
    by_ticker: Dict[str, GreekSet] = {}
    for pos in portfolio.list_open():
        sign = 1.0 if pos.direction == "long" else -1.0
        if pos.instrument == "equity":
            spot = price_data.get_last_price(pos.ticker) or pos.entry_price
            gs = GreekSet(price=spot, delta=sign * pos.quantity, gamma=0, theta=0, vega=0, rho=0)
            by_ticker[pos.ticker] = gs
            total.delta += gs.delta
            continue
        if pos.instrument == "etf":
            # ETF delta scales by signed leverage: a 3x bull ETF at 100 shares
            # contributes the same directional exposure as 300 shares of the
            # underlying; a 3x inverse contributes -300.
            spot = price_data.get_last_price(pos.ticker) or pos.entry_price
            lev = letfs.effective_leverage(pos.ticker)
            effective_delta = sign * pos.quantity * lev
            gs = GreekSet(price=spot, delta=effective_delta, gamma=0, theta=0, vega=0, rho=0)
            by_ticker[pos.ticker] = gs
            total.delta += gs.delta
            continue
        spot = price_data.get_last_price(pos.ticker) or 0.0
        if spot <= 0 or not pos.option_expiration or not pos.option_strike:
            continue
        from datetime import datetime
        try:
            exp_date = datetime.strptime(pos.option_expiration, "%Y-%m-%d").date()
            dte = max(0, (exp_date - datetime.utcnow().date()).days)
        except Exception:
            dte = 0
        t = dte_to_years(dte)
        # Solve IV from the mid we last observed; fall back to entry price.
        sigma = implied_vol(
            pos.entry_price, spot, pos.option_strike, t, risk_free_rate,
            option_type=pos.instrument,
        ) or 0.30
        gs = greeks(spot, pos.option_strike, t, risk_free_rate, sigma, pos.instrument)
        # Scale by 100 (contract multiplier) and signed quantity.
        scale = 100.0 * sign * pos.quantity
        scaled = GreekSet(
            price=gs.price,
            delta=gs.delta * scale,
            gamma=gs.gamma * scale,
            theta=gs.theta * scale / 365.0,
            vega=gs.vega * scale / 100.0,   # per 1% IV
            rho=gs.rho * scale / 100.0,     # per 1%
            iv=sigma,
        )
        by_ticker[pos.ticker] = scaled
        total.delta += scaled.delta
        total.gamma += scaled.gamma
        total.theta += scaled.theta
        total.vega += scaled.vega
        total.rho += scaled.rho

    breaches: List[str] = []
    if abs(total.vega) > vega_threshold:
        breaches.append(f"|vega| {abs(total.vega):.0f} > {vega_threshold:.0f}")
    if abs(total.gamma) > gamma_threshold:
        breaches.append(f"|gamma| {abs(total.gamma):.1f} > {gamma_threshold:.1f}")
    return PortfolioGreeks(
        delta=total.delta,
        gamma=total.gamma,
        theta=total.theta,
        vega=total.vega,
        rho=total.rho,
        by_ticker=by_ticker,
        thresholds_breached=breaches,
    )
