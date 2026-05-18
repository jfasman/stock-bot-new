"""Decompose a per-ticker score into the parts that produced it.

The scoring pipeline in `scorer.py` returns just a number and a list of reasons.
For an executive review we want the full breakdown: which sub-signal contributed
what, what the weights were, and where the indicators stood. This module re-runs
the same math but preserves every intermediate value as structured data.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from ..config import Config
from ..data import prices as price_data
from ..data.fundamentals import get_fundamentals
from ..sentiment.aggregator import aggregate
from . import indicators as ind
from .factors.fusion import compute_factor_composites
from .scorer import composite_score, read_technicals

log = logging.getLogger(__name__)


@dataclass
class ComponentContribution:
    """One row in the breakdown: a sub-signal, its raw value, weight, and dollar contribution."""
    name: str
    value: float            # raw signal in [-1, 1]
    weight: float           # weight applied in the blend
    contribution: float     # value * weight — units match the composite

    @property
    def pct_of_score(self) -> float:
        return self.contribution


@dataclass
class MatchedSetupInfo:
    """One row in the "matched setups" panel — every setup the ticker
    currently fits, with its walk-forward expectancy when available.
    Spec: roadmap §13.2 "Connection to score_explain".
    """
    name: str
    direction: str
    instrument_hint: str
    expected_holding_days: tuple[int, int]
    has_performance: bool                   # True iff a setup_performance row exists
    n_trades: int = 0
    expectancy: float = 0.0
    win_rate: float = 0.0
    last_validated_at: Optional[str] = None  # ISO string for JSON/dashboard convenience


@dataclass
class IndicatorReadings:
    rsi_14: float
    macd_hist_last: float
    sma_20: float
    sma_50: float
    bollinger_lower: float
    bollinger_upper: float
    bollinger_pct: float    # position within band, 0=lower, 1=upper
    avg_volume_20: float
    last_price: float

    def trend_label(self) -> str:
        if self.last_price > self.sma_20 > self.sma_50:
            return "strong uptrend (price > SMA20 > SMA50)"
        if self.last_price < self.sma_20 < self.sma_50:
            return "strong downtrend (price < SMA20 < SMA50)"
        if self.last_price > self.sma_50:
            return "above SMA50 (weak uptrend)"
        if self.last_price < self.sma_50:
            return "below SMA50 (weak downtrend)"
        return "mixed"

    def rsi_label(self) -> str:
        if self.rsi_14 >= 70:
            return "overbought"
        if self.rsi_14 <= 30:
            return "oversold"
        return "neutral"


@dataclass
class ScoreBreakdown:
    ticker: str
    last_price: float
    final_score: float
    classification: str                     # 'LONG' | 'SHORT' | 'NEUTRAL'
    tech_components: List[ComponentContribution]  # trend, momentum, breakout
    tech_composite: float
    sentiment_net: float
    sentiment_confidence: float
    sentiment_subscore: float               # net * confidence
    sentiment_weight: float                 # blend weight 0..1
    blended_components: List[ComponentContribution]  # technical block + sentiment block + factor block
    indicators: IndicatorReadings
    factor_scores: Optional[Dict[str, float]] = None   # per-factor when fundamentals available
    factor_composite: Optional[float] = None           # cross-sectional composite (roadmap §13.6)
    factor_weight: float = 0.0                          # blend weight applied to factor leg
    factor_contribution: float = 0.0                    # factor_weight × factor_composite
    reasons: List[str] = field(default_factory=list)
    thresholds: Dict[str, float] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    matched_setups: List["MatchedSetupInfo"] = field(default_factory=list)

    def formula(self) -> str:
        """Plain-English statement of the math used to produce final_score.

        The string always contains the literal "tech_composite" so callers that
        grep the formula text have a stable anchor.
        """
        sw = self.sentiment_weight if self.sentiment_confidence > 0 else 0.0
        fw = self.factor_weight if self.factor_composite is not None else 0.0
        tw = 1.0 - sw - fw
        if sw == 0 and fw == 0:
            return (
                "final = tech_composite (sentiment and factor legs both skipped)\n"
                f"       = {self.tech_composite:+.3f}"
            )
        head_terms = [f"{tw:.2f} × tech_composite"]
        num_terms = [f"{tw:.2f} × {self.tech_composite:+.3f}"]
        if sw > 0:
            head_terms.append(f"{sw:.2f} × (sent_net × sent_conf)")
            num_terms.append(f"{sw:.2f} × {self.sentiment_subscore:+.3f}")
        if fw > 0:
            head_terms.append(f"{fw:.2f} × factor_composite")
            num_terms.append(f"{fw:.2f} × {self.factor_composite:+.3f}")
        return (
            "final = " + " + ".join(head_terms) + "\n"
            "       = " + " + ".join(num_terms) + "\n"
            f"       = {self.final_score:+.3f}"
        )


def explain_score(cfg: Config, ticker: str, include_factors: bool = True) -> Optional[ScoreBreakdown]:
    """Re-run the scoring pipeline for `ticker` and return a full breakdown.

    Returns None if there isn't enough price history to score the ticker.
    """
    ticker = ticker.upper()
    df = price_data.get_history(ticker, period="6mo", interval="1d")
    tech = read_technicals(df, cfg)
    if tech is None:
        return None

    sentiments = aggregate(cfg, [ticker])
    sentiment = sentiments.get(ticker)
    factor_composites = compute_factor_composites(
        cfg,
        [ticker],
        fundamentals_loader=get_fundamentals,
        price_loader=lambda t: price_data.get_history(t, period="6mo", interval="1d"),
    )
    factor_composite_value = factor_composites.get(ticker)
    final, reasons = composite_score(
        cfg, tech, sentiment, factor_composite=factor_composite_value
    )

    # Re-derive raw indicators from the same dataframe so the dashboard can show numbers.
    close = df["Close"].astype(float)
    volume = df["Volume"].astype(float)
    sma20 = float(ind.sma(close, 20).iloc[-1])
    sma50 = float(ind.sma(close, 50).iloc[-1])
    _, _, macd_hist = ind.macd(close)
    lower_bb, _, upper_bb = ind.bollinger(close, 20, 2.0)
    lo, hi = float(lower_bb.iloc[-1]), float(upper_bb.iloc[-1])
    bb_pct = (tech.last - lo) / (hi - lo) if hi > lo else 0.5
    avg_vol = float(volume.rolling(20).mean().iloc[-1])
    indicators = IndicatorReadings(
        rsi_14=tech.rsi,
        macd_hist_last=float(macd_hist.iloc[-1]),
        sma_20=sma20,
        sma_50=sma50,
        bollinger_lower=lo,
        bollinger_upper=hi,
        bollinger_pct=float(np.clip(bb_pct, 0.0, 1.0)),
        avg_volume_20=avg_vol,
        last_price=tech.last,
    )

    # Technical sub-components and their hard-coded weights inside TechnicalRead.composite.
    tech_components = [
        ComponentContribution("trend",    tech.trend,    0.50, 0.50 * tech.trend),
        ComponentContribution("momentum", tech.momentum, 0.30, 0.30 * tech.momentum),
        ComponentContribution("breakout", tech.breakout, 0.20, 0.20 * tech.breakout),
    ]
    tech_composite = tech.composite

    sent_net = sentiment.net if sentiment else 0.0
    sent_conf = sentiment.confidence if sentiment else 0.0
    sent_sub = sent_net * sent_conf
    sent_w = float(cfg.strategy.get("sentiment_weight", 0.4))
    factor_w_cfg = float(cfg.strategy.get("factor_weight", 0.30))

    # The outer blend mirrors composite_score: tech absorbs whatever weight the
    # sentiment or factor leg gives up. See scorer.composite_score for the math.
    s_w = sent_w if sent_conf > 0 else 0.0
    f_w = factor_w_cfg if factor_composite_value is not None else 0.0
    t_w = 1.0 - s_w - f_w
    factor_sub = float(factor_composite_value) if factor_composite_value is not None else 0.0
    blended_components = [
        ComponentContribution("technical block", tech_composite, t_w, t_w * tech_composite),
        ComponentContribution("sentiment block", sent_sub,        s_w, s_w * sent_sub),
        ComponentContribution("factor block",    factor_sub,      f_w, f_w * factor_sub),
    ]

    min_long = float(cfg.strategy.get("min_score_to_trade", 0.55))
    min_short = float(cfg.strategy.get("bear_score_to_trade", -0.55))
    if final >= min_long:
        cls = "LONG"
    elif final <= min_short:
        cls = "SHORT"
    else:
        cls = "NEUTRAL"

    warnings: List[str] = []
    if not tech.volume_ok:
        warnings.append(f"Avg 20d volume {avg_vol:,.0f} below configured floor — liquidity risk.")
    if sent_conf < 0.2:
        warnings.append("Sentiment confidence is low; final score is essentially technical-only.")

    # Matched setups (roadmap §13.2). Pure read: matcher over already-fetched
    # tech/fundamentals/macro plus a perf-store lookup per match.
    matched_setups: List[MatchedSetupInfo] = []
    try:
        from ..data import macro as macro_data
        from ..data.fundamentals import get_fundamentals as _gf
        from ..ops.setup_performance import get_performance
        from .setups.matcher import match as match_setups
        fund = _gf(ticker)
        macro_snap = macro_data.snapshot()
        for s in match_setups(tech, fund, None, macro_snap):
            perf = get_performance(s.name)
            matched_setups.append(MatchedSetupInfo(
                name=s.name, direction=s.direction,
                instrument_hint=s.instrument_hint,
                expected_holding_days=s.expected_holding_days(),
                has_performance=perf is not None,
                n_trades=perf.n_trades if perf else 0,
                expectancy=perf.expectancy if perf else 0.0,
                win_rate=perf.win_rate if perf else 0.0,
                last_validated_at=perf.last_validated_at.isoformat() if perf else None,
            ))
    except Exception as exc:
        log.info("Setup matching unavailable for %s: %s", ticker, exc)

    # Per-factor breakdown for the dashboard Factor row. The composite that
    # feeds the blend comes from `compute_factor_composites` above (the
    # cross-sectional version against the configured peer pool); this single-
    # ticker call is only used to surface per-factor z-scores when fundamentals
    # are available.
    factor_scores: Optional[Dict[str, float]] = None
    if include_factors:
        try:
            from .factors import FactorWeights, score_universe
            fundamentals = {ticker: get_fundamentals(ticker)}
            report = score_universe(
                [ticker],
                fundamentals,
                {ticker: df},
                weights=FactorWeights(),
                sector_neutral=False,  # single-ticker run can't sector-neutralize
            )
            factor_scores = report.breakdown.get(ticker)
        except Exception as exc:
            log.info("Factor breakdown unavailable for %s: %s", ticker, exc)

    return ScoreBreakdown(
        ticker=ticker,
        last_price=tech.last,
        final_score=final,
        classification=cls,
        tech_components=tech_components,
        tech_composite=tech_composite,
        sentiment_net=sent_net,
        sentiment_confidence=sent_conf,
        sentiment_subscore=sent_sub,
        sentiment_weight=sent_w,
        blended_components=blended_components,
        indicators=indicators,
        factor_scores=factor_scores,
        factor_composite=factor_composite_value,
        factor_weight=factor_w_cfg,
        factor_contribution=f_w * factor_sub,
        reasons=reasons,
        thresholds={"min_long": min_long, "min_short": min_short},
        warnings=warnings,
        matched_setups=matched_setups,
    )
