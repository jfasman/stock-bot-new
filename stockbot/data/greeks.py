from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional


SQRT_2PI = math.sqrt(2 * math.pi)


def _phi(x: float) -> float:
    """Standard normal PDF."""
    return math.exp(-0.5 * x * x) / SQRT_2PI


def _N(x: float) -> float:
    """Standard normal CDF via erf."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


@dataclass
class GreekSet:
    price: float
    delta: float
    gamma: float
    theta: float          # per year; divide by 365 for per-day
    vega: float           # per 1.0 vol point (multiply by 0.01 for per 1%)
    rho: float            # per 1.0 rate (multiply by 0.01 for per 1%)
    iv: Optional[float] = None


def bs_price(
    spot: float,
    strike: float,
    t_years: float,
    rate: float,
    sigma: float,
    option_type: str = "call",
    dividend_yield: float = 0.0,
) -> float:
    """Black-Scholes-Merton price with continuous dividend yield."""
    if t_years <= 0 or sigma <= 0 or spot <= 0 or strike <= 0:
        intrinsic = max(0.0, spot - strike) if option_type == "call" else max(0.0, strike - spot)
        return intrinsic
    d1 = (math.log(spot / strike) + (rate - dividend_yield + 0.5 * sigma * sigma) * t_years) / (
        sigma * math.sqrt(t_years)
    )
    d2 = d1 - sigma * math.sqrt(t_years)
    if option_type == "call":
        return spot * math.exp(-dividend_yield * t_years) * _N(d1) - strike * math.exp(-rate * t_years) * _N(d2)
    return strike * math.exp(-rate * t_years) * _N(-d2) - spot * math.exp(-dividend_yield * t_years) * _N(-d1)


def greeks(
    spot: float,
    strike: float,
    t_years: float,
    rate: float,
    sigma: float,
    option_type: str = "call",
    dividend_yield: float = 0.0,
) -> GreekSet:
    if t_years <= 0 or sigma <= 0 or spot <= 0 or strike <= 0:
        intrinsic = max(0.0, spot - strike) if option_type == "call" else max(0.0, strike - spot)
        return GreekSet(price=intrinsic, delta=0.0, gamma=0.0, theta=0.0, vega=0.0, rho=0.0, iv=sigma)
    sqrt_t = math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (rate - dividend_yield + 0.5 * sigma * sigma) * t_years) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    pdf_d1 = _phi(d1)
    disc_r = math.exp(-rate * t_years)
    disc_q = math.exp(-dividend_yield * t_years)

    if option_type == "call":
        price = spot * disc_q * _N(d1) - strike * disc_r * _N(d2)
        delta = disc_q * _N(d1)
        theta = (
            -(spot * disc_q * pdf_d1 * sigma) / (2 * sqrt_t)
            - rate * strike * disc_r * _N(d2)
            + dividend_yield * spot * disc_q * _N(d1)
        )
        rho = strike * t_years * disc_r * _N(d2)
    else:
        price = strike * disc_r * _N(-d2) - spot * disc_q * _N(-d1)
        delta = -disc_q * _N(-d1)
        theta = (
            -(spot * disc_q * pdf_d1 * sigma) / (2 * sqrt_t)
            + rate * strike * disc_r * _N(-d2)
            - dividend_yield * spot * disc_q * _N(-d1)
        )
        rho = -strike * t_years * disc_r * _N(-d2)
    gamma = (disc_q * pdf_d1) / (spot * sigma * sqrt_t)
    vega = spot * disc_q * pdf_d1 * sqrt_t
    return GreekSet(price=price, delta=delta, gamma=gamma, theta=theta, vega=vega, rho=rho, iv=sigma)


def implied_vol(
    market_price: float,
    spot: float,
    strike: float,
    t_years: float,
    rate: float,
    option_type: str = "call",
    dividend_yield: float = 0.0,
    tol: float = 1e-4,
    max_iter: int = 100,
) -> Optional[float]:
    """Solve for IV via Newton-Raphson with a Brent-style bisection fallback."""
    if market_price <= 0 or t_years <= 0 or spot <= 0 or strike <= 0:
        return None
    sigma = 0.5  # starting guess
    for _ in range(max_iter):
        g = greeks(spot, strike, t_years, rate, sigma, option_type, dividend_yield)
        diff = g.price - market_price
        if abs(diff) < tol:
            return sigma
        if g.vega < 1e-8:
            break
        sigma -= diff / g.vega
        if sigma <= 0 or sigma > 5:
            sigma = max(0.01, min(5.0, sigma))
            break
    # Fallback: bisection on [0.01, 5.0].
    lo, hi = 0.01, 5.0
    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        p_mid = bs_price(spot, strike, t_years, rate, mid, option_type, dividend_yield)
        if abs(p_mid - market_price) < tol:
            return mid
        if p_mid > market_price:
            hi = mid
        else:
            lo = mid
    return None


def dte_to_years(dte: int) -> float:
    return max(dte, 0) / 365.0
