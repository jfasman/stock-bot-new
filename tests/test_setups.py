"""Tests for the setup library. Spec: roadmap §13.2.

Each setup is unit-tested independently. Fixture builds a neutral
TechnicalRead / OptionsContext / MacroSnapshot; each test toggles
exactly the inputs the setup cares about, asserts True/False, and
verifies one negative case per gating condition.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from stockbot.data.fundamentals import Fundamentals
from stockbot.data.macro import MacroSnapshot
from stockbot.strategy.scorer import TechnicalRead
from stockbot.strategy.setups import (
    ALL_SETUPS,
    BreakoutWithMomentum,
    IVCrushPremiumSell,
    MeanReversionOversold,
    OptionsContext,
    PullbackInUptrend,
    Setup,
)


def _tech(**overrides) -> TechnicalRead:
    base = TechnicalRead(
        trend=0.5, momentum=0.3, breakout=0.0, volume_ok=True,
        rsi=50.0, last=100.0,
        sma20=98.0, sma50=95.0, atr14=2.0,
        high_20d=99.0, volume_last=1_000_000.0, volume_20d_avg=800_000.0,
        macd_hist_last=0.5,
    )
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


def _macro() -> MacroSnapshot:
    return MacroSnapshot(
        vix=18.0, vix_3m=20.0, yield_2y=4.5, yield_10y=4.2, yield_30y=4.3,
        dxy=104.0, spx_close=4500.0, yield_curve_2s10s=-30.0, vix_term_structure=1.1,
    )


def _opts(**overrides) -> OptionsContext:
    base = dict(iv_rank=50.0, nearest_expiration_dte=30, has_catalyst_within_dte=False)
    base.update(overrides)
    return OptionsContext(**base)


def test_all_registered_setups_conform_to_protocol():
    # Smoke check: every registered setup honors the Setup Protocol.
    for s in ALL_SETUPS:
        assert isinstance(s, Setup)
        lo, hi = s.expected_holding_days()
        assert 0 < lo <= hi


# ── breakout_with_momentum ────────────────────────────────────────────────
def test_breakout_matches_when_all_conditions_hold():
    s = BreakoutWithMomentum()
    tech = _tech(last=101.0, high_20d=99.0, rsi=62.0,
                 macd_hist_last=0.3, volume_last=1_500_000, volume_20d_avg=800_000)
    assert s.matches(tech, None, None, _macro()) is True


def test_breakout_rejects_when_close_below_20d_high():
    s = BreakoutWithMomentum()
    tech = _tech(last=98.5, high_20d=99.0, rsi=62.0,
                 macd_hist_last=0.3, volume_last=1_500_000, volume_20d_avg=800_000)
    assert s.matches(tech, None, None, _macro()) is False


def test_breakout_rejects_on_weak_rsi():
    s = BreakoutWithMomentum()
    tech = _tech(last=101.0, high_20d=99.0, rsi=55.0,
                 macd_hist_last=0.3, volume_last=1_500_000, volume_20d_avg=800_000)
    assert s.matches(tech, None, None, _macro()) is False


def test_breakout_rejects_on_negative_macd_hist():
    s = BreakoutWithMomentum()
    tech = _tech(last=101.0, high_20d=99.0, rsi=62.0,
                 macd_hist_last=-0.1, volume_last=1_500_000, volume_20d_avg=800_000)
    assert s.matches(tech, None, None, _macro()) is False


def test_breakout_rejects_on_low_volume():
    s = BreakoutWithMomentum()
    tech = _tech(last=101.0, high_20d=99.0, rsi=62.0,
                 macd_hist_last=0.3, volume_last=900_000, volume_20d_avg=800_000)
    # 900k < 1.5 × 800k = 1.2M
    assert s.matches(tech, None, None, _macro()) is False


def test_breakout_rejects_on_missing_indicator_data():
    s = BreakoutWithMomentum()
    tech = _tech(high_20d=0.0)
    assert s.matches(tech, None, None, _macro()) is False


# ── pullback_in_uptrend ───────────────────────────────────────────────────
def test_pullback_matches_when_near_sma20_in_uptrend():
    s = PullbackInUptrend()
    # SMA20=100 > SMA50=95 (uptrend); last=100.5 is 0.25 ATR above SMA20; RSI=45 in band.
    tech = _tech(last=100.5, sma20=100.0, sma50=95.0, atr14=2.0, rsi=45.0)
    assert s.matches(tech, None, None, _macro()) is True


def test_pullback_rejects_when_not_in_uptrend():
    s = PullbackInUptrend()
    tech = _tech(last=100.5, sma20=95.0, sma50=100.0, atr14=2.0, rsi=45.0)
    assert s.matches(tech, None, None, _macro()) is False


def test_pullback_rejects_when_too_far_above_sma20():
    s = PullbackInUptrend()
    # 102.5 - 100 = 2.5 > 0.5 × 2 = 1 ATR proximity
    tech = _tech(last=102.5, sma20=100.0, sma50=95.0, atr14=2.0, rsi=45.0)
    assert s.matches(tech, None, None, _macro()) is False


def test_pullback_rejects_when_below_sma20():
    s = PullbackInUptrend()
    tech = _tech(last=99.0, sma20=100.0, sma50=95.0, atr14=2.0, rsi=45.0)
    assert s.matches(tech, None, None, _macro()) is False


def test_pullback_rejects_when_rsi_outside_band():
    s = PullbackInUptrend()
    for bad_rsi in (35.0, 60.0):
        tech = _tech(last=100.5, sma20=100.0, sma50=95.0, atr14=2.0, rsi=bad_rsi)
        assert s.matches(tech, None, None, _macro()) is False


# ── mean_reversion_oversold ───────────────────────────────────────────────
def test_mean_reversion_matches_when_oversold_and_far_below_sma():
    s = MeanReversionOversold()
    # RSI < 30, SMA20 - last = 3.0 >= 1.0 × ATR(2.0)? 3.0 >= 2.0 yes.
    tech = _tech(last=97.0, sma20=100.0, atr14=2.0, rsi=28.0)
    assert s.matches(tech, None, None, _macro()) is True


def test_mean_reversion_rejects_when_rsi_not_oversold():
    s = MeanReversionOversold()
    tech = _tech(last=97.0, sma20=100.0, atr14=2.0, rsi=35.0)
    assert s.matches(tech, None, None, _macro()) is False


def test_mean_reversion_rejects_when_price_close_to_sma():
    s = MeanReversionOversold()
    # SMA20 - last = 1.0 < 1.0 × ATR(2.0)
    tech = _tech(last=99.0, sma20=100.0, atr14=2.0, rsi=28.0)
    assert s.matches(tech, None, None, _macro()) is False


def test_mean_reversion_permissive_on_missing_fundamentals():
    # Documented choice: no earnings data => no earnings in buffer (permissive).
    s = MeanReversionOversold()
    tech = _tech(last=97.0, sma20=100.0, atr14=2.0, rsi=28.0)
    assert s.matches(tech, None, None, _macro()) is True
    assert s.matches(tech, Fundamentals(ticker="AAPL"), None, _macro()) is True


# ── iv_crush_premium_sell ─────────────────────────────────────────────────
def test_iv_crush_matches_high_iv_no_catalyst():
    s = IVCrushPremiumSell()
    opts = _opts(iv_rank=75.0, has_catalyst_within_dte=False)
    assert s.matches(_tech(), None, opts, _macro()) is True


def test_iv_crush_rejects_low_iv():
    s = IVCrushPremiumSell()
    opts = _opts(iv_rank=60.0)
    assert s.matches(_tech(), None, opts, _macro()) is False


def test_iv_crush_rejects_on_catalyst_within_dte():
    s = IVCrushPremiumSell()
    opts = _opts(iv_rank=80.0, has_catalyst_within_dte=True)
    assert s.matches(_tech(), None, opts, _macro()) is False


def test_iv_crush_rejects_when_options_unavailable():
    s = IVCrushPremiumSell()
    assert s.matches(_tech(), None, None, _macro()) is False
    assert s.matches(_tech(), None, _opts(iv_rank=None), _macro()) is False
