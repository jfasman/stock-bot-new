from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from ..config import Config
from ..sentiment.aggregator import AggregateSentiment
from . import indicators as ind


@dataclass
class TechnicalRead:
    trend: float          # -1..1 — direction & strength of price trend
    momentum: float       # -1..1 — RSI/MACD bias
    breakout: float       # -1..1 — proximity to range extremes
    volume_ok: bool
    rsi: float
    last: float
    notes: list[str] = field(default_factory=list)
    # Raw indicators — exposed so the setup library (§13.2) can pattern-match
    # without re-running indicator math. Default 0.0 keeps old fixtures working.
    sma20: float = 0.0
    sma50: float = 0.0
    atr14: float = 0.0
    high_20d: float = 0.0
    volume_last: float = 0.0
    volume_20d_avg: float = 0.0
    macd_hist_last: float = 0.0

    @property
    def composite(self) -> float:
        # Weighted blend of three technical signals.
        return 0.5 * self.trend + 0.3 * self.momentum + 0.2 * self.breakout


def read_technicals(df: pd.DataFrame, cfg: Config) -> Optional[TechnicalRead]:
    if df is None or df.empty or len(df) < 50:
        return None
    close = df["Close"].astype(float)
    volume = df["Volume"].astype(float)

    sma20 = ind.sma(close, 20)
    sma50 = ind.sma(close, 50)
    rsi14 = ind.rsi(close, 14)
    _, _, macd_hist = ind.macd(close)
    lower_bb, _, upper_bb = ind.bollinger(close, 20, 2.0)
    # ATR needs High/Low; tolerate dataframes that only carry Close/Volume.
    atr14 = ind.atr(df, 14) if {"High", "Low"}.issubset(df.columns) else None
    high_20d = df["High"].rolling(20).max() if "High" in df.columns else close.rolling(20).max()

    last = float(close.iloc[-1])
    s20 = float(sma20.iloc[-1])
    s50 = float(sma50.iloc[-1])
    rsi_last = float(rsi14.iloc[-1]) if not np.isnan(rsi14.iloc[-1]) else 50.0

    # Trend: based on relationship of price to moving averages.
    trend_raw = 0.0
    if last > s20 > s50:
        trend_raw = 1.0
    elif last < s20 < s50:
        trend_raw = -1.0
    elif last > s50:
        trend_raw = 0.4
    elif last < s50:
        trend_raw = -0.4
    # Strengthen by slope of the 20-day.
    sma20_slope = (s20 - float(sma20.iloc[-5])) / max(s20, 1e-6)
    trend = float(np.clip(trend_raw + 5 * sma20_slope, -1.0, 1.0))

    # Momentum: RSI scaled around 50 plus MACD histogram sign.
    rsi_component = (rsi_last - 50) / 30  # 30 -> ~-0.67, 70 -> ~0.67
    macd_component = 0.3 if macd_hist.iloc[-1] > 0 else -0.3
    momentum = float(np.clip(rsi_component + macd_component, -1.0, 1.0))

    # Breakout: position within the Bollinger band, clamped.
    bb_upper = float(upper_bb.iloc[-1])
    bb_lower = float(lower_bb.iloc[-1])
    if bb_upper > bb_lower:
        rel = (last - bb_lower) / (bb_upper - bb_lower) * 2 - 1
    else:
        rel = 0.0
    breakout = float(np.clip(rel, -1.0, 1.0))

    avg_vol = float(volume.rolling(20).mean().iloc[-1])
    volume_ok = avg_vol >= float(cfg.strategy.get("min_avg_volume", 0))

    notes = []
    rsi_os = cfg.strategy.get("rsi_oversold", 30)
    rsi_ob = cfg.strategy.get("rsi_overbought", 70)
    if rsi_last <= rsi_os:
        notes.append(f"RSI {rsi_last:.1f} oversold")
    elif rsi_last >= rsi_ob:
        notes.append(f"RSI {rsi_last:.1f} overbought")
    if last > bb_upper:
        notes.append("price above upper Bollinger")
    elif last < bb_lower:
        notes.append("price below lower Bollinger")

    return TechnicalRead(
        trend=trend,
        momentum=momentum,
        breakout=breakout,
        volume_ok=volume_ok,
        rsi=rsi_last,
        last=last,
        notes=notes,
        sma20=s20,
        sma50=s50,
        atr14=float(atr14.iloc[-1]) if atr14 is not None and not np.isnan(atr14.iloc[-1]) else 0.0,
        high_20d=float(high_20d.iloc[-1]) if not np.isnan(high_20d.iloc[-1]) else 0.0,
        volume_last=float(volume.iloc[-1]),
        volume_20d_avg=avg_vol if not np.isnan(avg_vol) else 0.0,
        macd_hist_last=float(macd_hist.iloc[-1]) if not np.isnan(macd_hist.iloc[-1]) else 0.0,
    )


def composite_score(
    cfg: Config,
    tech: TechnicalRead,
    sentiment: Optional[AggregateSentiment],
) -> tuple[float, list[str]]:
    """Blend technical and sentiment reads into a single signed score in [-1, 1]."""
    tech_score = tech.composite
    sent_weight = float(cfg.strategy.get("sentiment_weight", 0.4))
    if sentiment and sentiment.confidence > 0:
        sent_score = sentiment.net * sentiment.confidence
        blended = (1 - sent_weight) * tech_score + sent_weight * sent_score
    else:
        blended = tech_score
    reasons = list(tech.notes)
    if sentiment and sentiment.confidence > 0:
        reasons.append(
            f"sentiment {sentiment.net:+.2f} (conf {sentiment.confidence:.2f})"
        )
    if not tech.volume_ok:
        reasons.append("low liquidity")
    return float(np.clip(blended, -1.0, 1.0)), reasons
