from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, List, Optional

from ..config import Config
from ..data import options as opt_data
from ..data import prices as price_data
from ..sentiment.aggregator import AggregateSentiment, aggregate
from . import leveraged_etfs as letfs
from .scorer import TechnicalRead, composite_score, read_technicals

log = logging.getLogger(__name__)


@dataclass
class Idea:
    ticker: str
    direction: str            # 'long' | 'short' (informational; we use long puts for short)
    instrument: str           # 'equity' | 'call' | 'put' | 'etf'
    score: float              # signed composite, -1..1
    last_price: float
    rsi: float
    reasons: List[str]
    sentiment_net: float
    sentiment_confidence: float
    option_symbol: Optional[str] = None
    option_strike: Optional[float] = None
    option_expiration: Optional[str] = None
    option_mid: Optional[float] = None
    option_dte: Optional[int] = None
    leverage_factor: float = 1.0          # signed: +3 TQQQ, -3 SQQQ, +1 plain equity
    alternative_to: Optional[str] = None  # underlying this ETF is substituting for

    def headline(self) -> str:
        if self.instrument == "equity":
            return f"{self.ticker} LONG (score {self.score:+.2f})"
        if self.instrument == "etf":
            lev = f"{self.leverage_factor:+.0f}x"
            via = f" via {self.alternative_to}" if self.alternative_to and self.alternative_to != self.ticker else ""
            return f"{self.ticker} ETF {lev}{via} (score {self.score:+.2f})"
        side = "CALL" if self.instrument == "call" else "PUT"
        strike = f"{self.option_strike:.1f}" if self.option_strike else "?"
        exp = self.option_expiration or "?"
        return f"{self.ticker} {side} {strike} {exp} (score {self.score:+.2f})"


def _build_option_idea(
    cfg: Config,
    ticker: str,
    direction: str,
    spot: float,
    score: float,
    tech: TechnicalRead,
    sentiment: Optional[AggregateSentiment],
    reasons: List[str],
) -> Optional[Idea]:
    opts = cfg.options
    if not opts.get("enabled", True):
        return None
    contract = opt_data.pick_contract(
        ticker=ticker,
        direction=direction,
        spot=spot,
        dte_min=int(opts.get("prefer_dte_min", 21)),
        dte_max=int(opts.get("prefer_dte_max", 45)),
        moneyness=0.0,
    )
    if contract is None or contract.mid <= 0:
        return None
    return Idea(
        ticker=ticker,
        direction="long" if direction == "call" else "short",
        instrument=direction,
        score=score,
        last_price=spot,
        rsi=tech.rsi,
        reasons=reasons,
        sentiment_net=sentiment.net if sentiment else 0.0,
        sentiment_confidence=sentiment.confidence if sentiment else 0.0,
        option_symbol=contract.symbol,
        option_strike=contract.strike,
        option_expiration=contract.expiration,
        option_mid=contract.mid,
        option_dte=contract.dte,
    )


def _build_etf_idea(
    underlying: str,
    etf: letfs.LeveragedETF,
    score: float,
    tech: TechnicalRead,
    sentiment: Optional[AggregateSentiment],
    reasons: List[str],
    horizon_days: int,
) -> Optional[Idea]:
    spot = price_data.get_last_price(etf.symbol)
    if spot is None or spot <= 0:
        return None
    etf_reasons = list(reasons)
    label = "leveraged" if etf.is_leveraged else "1x"
    side = "inverse" if etf.is_inverse else "long"
    etf_reasons.append(
        f"{label} {side} ETF ({etf.leverage:+.0f}x {etf.underlying}) — {etf.description}"
    )
    warn = letfs.decay_warning(etf.leverage, horizon_days)
    if warn:
        etf_reasons.append(warn)
    return Idea(
        ticker=etf.symbol,
        direction="long",         # we always hold the ETF long; inverse direction is baked into leverage
        instrument="etf",
        score=score,
        last_price=float(spot),
        rsi=tech.rsi,
        reasons=etf_reasons,
        sentiment_net=sentiment.net if sentiment else 0.0,
        sentiment_confidence=sentiment.confidence if sentiment else 0.0,
        leverage_factor=etf.leverage,
        alternative_to=underlying,
    )


def generate_ideas(cfg: Config, tickers: Iterable[str]) -> List[Idea]:
    tickers = [t.upper() for t in tickers]
    sentiments = aggregate(cfg, tickers)
    min_long = float(cfg.strategy.get("min_score_to_trade", 0.55))
    min_short = float(cfg.strategy.get("bear_score_to_trade", -0.55))

    letf_cfg = cfg.raw.get("leveraged_etfs", {}) if hasattr(cfg, "raw") else {}
    letf_enabled = bool(letf_cfg.get("enabled", True))
    max_lev = float(letf_cfg.get("max_leverage_factor", 3.0))
    horizon = int(cfg.strategy.get("horizon_days_max", 20))
    prefer_inverse = bool(letf_cfg.get("prefer_inverse_over_short", True))
    high_conviction = float(letf_cfg.get("leveraged_long_threshold", 0.85))

    ideas: List[Idea] = []
    for t in tickers:
        df = price_data.get_history(t, period="6mo", interval="1d")
        tech = read_technicals(df, cfg)
        if tech is None:
            log.info("Skip %s: not enough price history", t)
            continue
        sentiment = sentiments.get(t)
        score, reasons = composite_score(cfg, tech, sentiment)

        if score >= min_long:
            ideas.append(
                Idea(
                    ticker=t,
                    direction="long",
                    instrument="equity",
                    score=score,
                    last_price=tech.last,
                    rsi=tech.rsi,
                    reasons=reasons,
                    sentiment_net=sentiment.net if sentiment else 0.0,
                    sentiment_confidence=sentiment.confidence if sentiment else 0.0,
                )
            )
            opt_idea = _build_option_idea(cfg, t, "call", tech.last, score, tech, sentiment, reasons)
            if opt_idea:
                ideas.append(opt_idea)
            # Leveraged long ETF alternative for very-high-conviction longs only.
            if letf_enabled and score >= high_conviction:
                alts = letfs.find_alternatives(t, "bull", max_leverage=max_lev)
                # Pick the most leveraged option within the cap.
                if alts:
                    etf_idea = _build_etf_idea(t, alts[-1], score, tech, sentiment, reasons, horizon)
                    if etf_idea:
                        ideas.append(etf_idea)
        elif score <= min_short:
            opt_idea = _build_option_idea(cfg, t, "put", tech.last, score, tech, sentiment, reasons)
            if opt_idea:
                ideas.append(opt_idea)
            # Inverse ETF alternatives for bearish views: prefer 1x for cleaner exposure,
            # offer a leveraged version only at high conviction.
            if letf_enabled:
                alts = letfs.find_alternatives(t, "bear", max_leverage=max_lev)
                if alts:
                    # Always offer the lowest-leverage (typically -1x) bear ETF.
                    primary = alts[0]
                    etf_idea = _build_etf_idea(t, primary, score, tech, sentiment, reasons, horizon)
                    if etf_idea:
                        ideas.append(etf_idea)
                    # Also offer a leveraged inverse if conviction is very high.
                    if abs(score) >= high_conviction and len(alts) > 1:
                        leveraged_alt = alts[-1]
                        if leveraged_alt.symbol != primary.symbol:
                            etf_idea2 = _build_etf_idea(t, leveraged_alt, score, tech, sentiment, reasons, horizon)
                            if etf_idea2:
                                ideas.append(etf_idea2)

    ideas.sort(key=lambda i: abs(i.score), reverse=True)
    return ideas
