"""Leveraged and inverse ETF registry and substitution helpers.

When the strategy wants bear exposure we can express it three ways:
  (a) a long PUT option
  (b) a short equity position (requires margin/borrow, no upside cap)
  (c) a long position in an inverse ETF (no margin, capped at 100% loss)

Option (c) is operationally simplest for a paper-trading research bot and is
often the cleanest expression for retail-style horizons. The trade-off is
volatility drag: daily-rebalanced leveraged products decay in choppy markets
and are explicitly not buy-and-hold instruments. The decay warning below is
the CFO-level talking point — leverage is a horizon question, not just a
size question.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional


@dataclass(frozen=True)
class LeveragedETF:
    symbol: str
    underlying: str          # e.g. 'SPY', 'QQQ', 'XLF' — the index/sector tracked
    leverage: float          # signed multiplier: +3 for TQQQ, -3 for SQQQ, -1 for SH
    family: str              # 'broad', 'tech', 'semi', 'financial', 'small_cap', 'energy', 'gold'
    description: str

    @property
    def direction(self) -> str:
        return "bull" if self.leverage > 0 else "bear"

    @property
    def is_inverse(self) -> bool:
        return self.leverage < 0

    @property
    def is_leveraged(self) -> bool:
        return abs(self.leverage) > 1.0


# Curated registry of common US-listed leveraged/inverse ETFs. Kept deliberately
# small — these are the products with deep liquidity and tight spreads. Adding
# obscure 2x/3x niche products without verifying ADV is how a strategy gets
# stuck holding paper at a wide spread.
_REGISTRY: List[LeveragedETF] = [
    # Broad market (S&P 500)
    LeveragedETF("SPY",  "SPY", +1.0, "broad", "S&P 500 (reference)"),
    LeveragedETF("SH",   "SPY", -1.0, "broad", "ProShares Short S&P 500 (-1x)"),
    LeveragedETF("SDS",  "SPY", -2.0, "broad", "ProShares UltraShort S&P 500 (-2x)"),
    LeveragedETF("SPXU", "SPY", -3.0, "broad", "ProShares UltraPro Short S&P 500 (-3x)"),
    LeveragedETF("SSO",  "SPY", +2.0, "broad", "ProShares Ultra S&P 500 (+2x)"),
    LeveragedETF("UPRO", "SPY", +3.0, "broad", "ProShares UltraPro S&P 500 (+3x)"),
    # Nasdaq 100
    LeveragedETF("QQQ",  "QQQ", +1.0, "tech", "Nasdaq 100 (reference)"),
    LeveragedETF("PSQ",  "QQQ", -1.0, "tech", "ProShares Short QQQ (-1x)"),
    LeveragedETF("QID",  "QQQ", -2.0, "tech", "ProShares UltraShort QQQ (-2x)"),
    LeveragedETF("SQQQ", "QQQ", -3.0, "tech", "ProShares UltraPro Short QQQ (-3x)"),
    LeveragedETF("QLD",  "QQQ", +2.0, "tech", "ProShares Ultra QQQ (+2x)"),
    LeveragedETF("TQQQ", "QQQ", +3.0, "tech", "ProShares UltraPro QQQ (+3x)"),
    # Dow
    LeveragedETF("DIA",  "DIA", +1.0, "broad", "Dow 30 (reference)"),
    LeveragedETF("DOG",  "DIA", -1.0, "broad", "ProShares Short Dow30 (-1x)"),
    LeveragedETF("SDOW", "DIA", -3.0, "broad", "ProShares UltraPro Short Dow30 (-3x)"),
    LeveragedETF("UDOW", "DIA", +3.0, "broad", "ProShares UltraPro Dow30 (+3x)"),
    # Russell 2000 small caps
    LeveragedETF("IWM",  "IWM", +1.0, "small_cap", "Russell 2000 (reference)"),
    LeveragedETF("RWM",  "IWM", -1.0, "small_cap", "ProShares Short Russell2000 (-1x)"),
    LeveragedETF("TZA",  "IWM", -3.0, "small_cap", "Direxion Daily Small Cap Bear 3x"),
    LeveragedETF("TNA",  "IWM", +3.0, "small_cap", "Direxion Daily Small Cap Bull 3x"),
    # Semiconductors
    LeveragedETF("SOXX", "SOXX", +1.0, "semi", "iShares Semiconductor (reference)"),
    LeveragedETF("SOXS", "SOXX", -3.0, "semi", "Direxion Daily Semis Bear 3x"),
    LeveragedETF("SOXL", "SOXX", +3.0, "semi", "Direxion Daily Semis Bull 3x"),
    # Financials
    LeveragedETF("XLF",  "XLF", +1.0, "financial", "Financials Select SPDR (reference)"),
    LeveragedETF("FAZ",  "XLF", -3.0, "financial", "Direxion Daily Financial Bear 3x"),
    LeveragedETF("FAS",  "XLF", +3.0, "financial", "Direxion Daily Financial Bull 3x"),
    # Energy
    LeveragedETF("XLE",  "XLE", +1.0, "energy", "Energy Select SPDR (reference)"),
    LeveragedETF("ERY",  "XLE", -2.0, "energy", "Direxion Daily Energy Bear 2x"),
    LeveragedETF("ERX",  "XLE", +2.0, "energy", "Direxion Daily Energy Bull 2x"),
    # Gold miners
    LeveragedETF("GDX",  "GDX", +1.0, "gold", "Gold Miners ETF (reference)"),
    LeveragedETF("DUST", "GDX", -2.0, "gold", "Direxion Daily Gold Miners Bear 2x"),
    LeveragedETF("NUGT", "GDX", +2.0, "gold", "Direxion Daily Gold Miners Bull 2x"),
]


_BY_SYMBOL = {e.symbol: e for e in _REGISTRY}

# Map well-known single names to their best-fit sector reference so a bearish
# view on, say, NVDA can suggest semis bear ETFs and not just SPY-bear.
_SECTOR_PROXY = {
    "NVDA": "SOXX", "AMD": "SOXX", "AVGO": "SOXX", "INTC": "SOXX", "MU": "SOXX",
    "TSM": "SOXX", "QCOM": "SOXX", "ASML": "SOXX",
    "AAPL": "QQQ", "MSFT": "QQQ", "GOOGL": "QQQ", "GOOG": "QQQ", "AMZN": "QQQ",
    "META": "QQQ", "NFLX": "QQQ", "TSLA": "QQQ", "ADBE": "QQQ", "CRM": "QQQ",
    "JPM": "XLF", "BAC": "XLF", "WFC": "XLF", "GS": "XLF", "MS": "XLF", "C": "XLF",
    "XOM": "XLE", "CVX": "XLE", "COP": "XLE", "SLB": "XLE", "EOG": "XLE",
    "GLD": "GDX", "GOLD": "GDX", "NEM": "GDX",
}


def get(symbol: str) -> Optional[LeveragedETF]:
    """Return registry entry for a symbol, or None if unknown."""
    return _BY_SYMBOL.get(symbol.upper())


def effective_leverage(symbol: str) -> float:
    """Signed leverage factor for a symbol. Unknown symbols default to +1."""
    e = get(symbol)
    return e.leverage if e else 1.0


def is_registered(symbol: str) -> bool:
    return symbol.upper() in _BY_SYMBOL


def _underlying_for(ticker: str) -> str:
    """Resolve an arbitrary ticker to the best registry 'underlying'."""
    t = ticker.upper()
    if t in _BY_SYMBOL:
        return _BY_SYMBOL[t].underlying
    return _SECTOR_PROXY.get(t, "SPY")  # SPY is the catch-all broad fallback


def find_alternatives(
    ticker: str,
    direction: str,
    max_leverage: float = 3.0,
) -> List[LeveragedETF]:
    """Return ETF alternatives expressing `direction` exposure to `ticker`'s sector.

    direction: 'bull' (long exposure) or 'bear' (short exposure)
    max_leverage: cap on |leverage|. Set to 1.0 to exclude leveraged products.
    Sorted by ascending |leverage| so the 1x inverse appears before 3x.
    """
    if direction not in ("bull", "bear"):
        raise ValueError(f"direction must be 'bull' or 'bear', got {direction!r}")
    underlying = _underlying_for(ticker)
    out = [
        e for e in _REGISTRY
        if e.underlying == underlying
        and e.direction == direction
        and e.leverage != 1.0   # skip the reference 1x bull underlying itself
        and abs(e.leverage) <= max_leverage
    ]
    out.sort(key=lambda e: abs(e.leverage))
    return out


def decay_warning(leverage: float, holding_days: int) -> Optional[str]:
    """Return a human-readable warning if leveraged ETF is held past a safe horizon.

    The math: daily-rebalanced 2x/3x products lose to volatility drag of roughly
    `0.5 * L * (L - 1) * sigma^2 * t` per period vs the path-independent target.
    At sigma=2%/day and L=3 over 30 days, that's already a couple of percent of
    drag even before any directional move. We don't compute it exactly here —
    the warning is a horizon flag for the human-in-the-loop.
    """
    L = abs(leverage)
    if L <= 1.0:
        return None
    if L >= 3.0 and holding_days > 5:
        return (
            f"⚠ {L:.0f}x leverage held {holding_days}d — volatility drag is "
            "material; these products are designed for single-day exposure."
        )
    if L >= 2.0 and holding_days > 10:
        return (
            f"⚠ {L:.0f}x leverage held {holding_days}d — monitor for "
            "compounding decay vs the underlying."
        )
    return None


def list_registry(family: Optional[str] = None) -> List[LeveragedETF]:
    """Return all registered ETFs, optionally filtered by family."""
    if family is None:
        return list(_REGISTRY)
    return [e for e in _REGISTRY if e.family == family]


def gross_leverage(positions: Iterable[dict]) -> float:
    """Compute portfolio gross leverage from a list of {ticker, weight} dicts.

    Each position's contribution is |weight * leverage_factor|. A book that is
    50% in cash, 25% in TQQQ (3x), 25% in SH (-1x) has gross leverage
    = 0.25*3 + 0.25*1 = 1.0, even though only 50% of capital is deployed.
    """
    total = 0.0
    for p in positions:
        w = abs(float(p.get("weight", 0.0)))
        lev = abs(effective_leverage(p["ticker"]))
        total += w * lev
    return total
