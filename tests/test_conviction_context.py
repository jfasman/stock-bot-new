"""Tests for the conviction context assembler."""
from __future__ import annotations

from datetime import datetime, timedelta

from stockbot.config import Config
from stockbot.data import macro as macro_data
from stockbot.ops import conviction_log
from stockbot.strategy.conviction import (
    ConvictionPick,
    GateResult,
    MatchedSetup,
    TimeToAct,
    evaluate,
)
from stockbot.strategy.conviction_context import (
    build_context,
    build_thresholds,
    to_gate_macro,
)
from stockbot.strategy.ideas import Idea


def _cfg() -> Config:
    return Config(raw={
        "strategy": {"min_score_to_trade": 0.55},
        "conviction": {
            "notify_threshold": 0.70,
            "cooldown_hours": 24,
            "min_setup_trades": 20,
            "setup_validation_max_age_days": 180,
            "regime": {
                "vix_max_long": 28,
                "vix_max_short": 45,
                "yield_curve_inverted_blocks": ["breakout_with_momentum"],
                "vix_cap_exempt_setups": ["mean_reversion_oversold"],
            },
            "data_quality": {
                "max_price_age_seconds": 900,
                "max_fundamentals_age_seconds": 604800,
            },
        },
    })


def _raw_macro(**overrides) -> macro_data.MacroSnapshot:
    base = dict(
        vix=18.0, vix_3m=20.0, yield_2y=4.5, yield_10y=4.2, yield_30y=4.3,
        dxy=104.0, spx_close=4500.0, yield_curve_2s10s=-30.0, vix_term_structure=1.1,
    )
    base.update(overrides)
    return macro_data.MacroSnapshot(**base)


def _idea(ticker: str = "AAPL", score: float = 0.80) -> Idea:
    return Idea(
        ticker=ticker, direction="long", instrument="equity",
        score=score, last_price=200.0, rsi=55.0,
        reasons=["test"], sentiment_net=0.0, sentiment_confidence=0.0,
    )


def test_build_thresholds_pulls_from_config_with_min_score_mirror():
    th = build_thresholds(_cfg())
    assert th.notify_threshold == 0.70
    assert th.min_score == 0.55                # abs-mirror of strategy.min_score_to_trade
    assert th.cooldown_hours == 24
    assert th.vix_max_long == 28
    assert th.yield_curve_inverted_blocks == ("breakout_with_momentum",)
    assert th.vix_cap_exempt_setups == ("mean_reversion_oversold",)


def test_build_thresholds_handles_negative_min_score_to_trade():
    # bear_score_to_trade is -0.55; min_score should be 0.55 (abs).
    cfg = Config(raw={
        "strategy": {"min_score_to_trade": -0.55},
        "conviction": {"notify_threshold": 0.70},
    })
    assert build_thresholds(cfg).min_score == 0.55


def test_to_gate_macro_maps_fields():
    gm = to_gate_macro(_raw_macro(vix=22.5, yield_curve_2s10s=-15.0))
    assert gm.vix == 22.5
    assert gm.yield_curve_slope_bps == -15.0


def test_to_gate_macro_defends_against_missing_vix():
    # Missing VIX should not silently pass long-vix-cap; collapse to +inf.
    gm = to_gate_macro(_raw_macro(vix=None))
    assert gm.vix == float("inf")
    # Missing curve becomes flat (no inversion block fires).
    gm2 = to_gate_macro(_raw_macro(yield_curve_2s10s=None))
    assert gm2.yield_curve_slope_bps == 0.0


def test_build_context_injection_path_skips_network():
    # Passing macro= directly must not call data.macro.snapshot().
    now = datetime(2026, 5, 17, 14, 30)
    ctx = build_context(_cfg(), _idea(), now=now, macro=_raw_macro())
    assert ctx.now == now
    assert ctx.macro.vix == 18.0
    assert ctx.factor_composite is None              # Cluster 1 stub
    assert ctx.matched_setup is None                 # Cluster 1 stub
    assert ctx.last_notified_at is None              # empty log
    assert ctx.price_age_seconds == 0.0              # data-quality stubbed fresh
    assert ctx.has_unaccounted_corporate_action is False


def test_build_context_reads_last_notified_at_from_log():
    # Persist a pass, then assemble — cooldown anchor should come back.
    idea = _idea("AAPL")
    ts = datetime(2026, 5, 17, 10, 0)
    pick = ConvictionPick(
        idea=idea, gates_passed=("score",),
        confidence_band=(0.7, 0.8), time_to_act=TimeToAct.OPEN_TOMORROW,
    )
    conviction_log.log_evaluation(
        idea,
        {n: GateResult(True, "ok") for n in (
            "score", "factor_agreement", "regime",
            "setup_validated", "cooldown", "data_quality",
        )},
        pick=pick, ts=ts,
    )
    ctx = build_context(_cfg(), idea, now=ts + timedelta(hours=1), macro=_raw_macro())
    assert ctx.last_notified_at == ts


def test_build_context_accepts_injected_setup_and_factor():
    # Cluster 2/4 forward-compat: caller can inject matched_setup and factor_composite.
    setup = MatchedSetup(
        name="pullback_in_uptrend", direction="long",
        n_trades=40, expectancy=0.25,
        last_validated_at=datetime(2026, 5, 1),
    )
    ctx = build_context(
        _cfg(), _idea(),
        now=datetime(2026, 5, 17), macro=_raw_macro(),
        matched_setup=setup, factor_composite=0.4,
    )
    assert ctx.matched_setup is setup
    assert ctx.factor_composite == 0.4


def test_build_context_feeds_a_passing_evaluation_end_to_end():
    # The whole pipeline with sensible inputs should green-light a strong idea.
    setup = MatchedSetup(
        name="pullback_in_uptrend", direction="long",
        n_trades=40, expectancy=0.25,
        last_validated_at=datetime(2026, 5, 1),
    )
    ctx = build_context(
        _cfg(), _idea(score=0.80),
        now=datetime(2026, 5, 17), macro=_raw_macro(),
        matched_setup=setup, factor_composite=0.4,
    )
    pick, verdicts = evaluate(_idea(score=0.80), ctx)
    assert pick is not None
    assert all(v.passed for v in verdicts.values())


def test_build_context_runs_matcher_when_tech_supplied_and_perf_exists():
    """When tech is supplied AND setup_performance has a row for the
    matched setup, the assembler derives MatchedSetup automatically."""
    from stockbot.ops.setup_performance import SetupPerformance, upsert_performance
    from stockbot.strategy.scorer import TechnicalRead

    # Tech reads as a breakout: last > 20d high, RSI ≥ 60, MACD > 0, volume spike.
    tech = TechnicalRead(
        trend=0.8, momentum=0.6, breakout=0.7, volume_ok=True,
        rsi=65.0, last=101.0,
        sma20=98.0, sma50=95.0, atr14=2.0,
        high_20d=99.0, volume_last=1_500_000, volume_20d_avg=800_000,
        macd_hist_last=0.3,
    )
    upsert_performance(SetupPerformance(
        setup_name="breakout_with_momentum", direction="long",
        n_trades=40, win_rate=0.55, avg_r=0.4, expectancy=0.25,
        sharpe=1.2, last_validated_at=datetime(2026, 5, 1),
    ))
    ctx = build_context(
        _cfg(), _idea(),
        now=datetime(2026, 5, 17), macro=_raw_macro(),
        tech=tech,
    )
    assert ctx.matched_setup is not None
    assert ctx.matched_setup.name == "breakout_with_momentum"
    assert ctx.matched_setup.n_trades == 40
    assert ctx.matched_setup.expectancy == 0.25


def test_build_context_matched_setup_is_zero_row_when_no_perf():
    """Matched but no walk-forward data → MatchedSetup with n_trades=0,
    so setup_validated gate fails closed on insufficient sample."""
    from stockbot.strategy.scorer import TechnicalRead

    tech = TechnicalRead(
        trend=0.8, momentum=0.6, breakout=0.7, volume_ok=True,
        rsi=65.0, last=101.0,
        sma20=98.0, sma50=95.0, atr14=2.0,
        high_20d=99.0, volume_last=1_500_000, volume_20d_avg=800_000,
        macd_hist_last=0.3,
    )
    ctx = build_context(
        _cfg(), _idea(),
        now=datetime(2026, 5, 17), macro=_raw_macro(),
        tech=tech,
    )
    assert ctx.matched_setup is not None
    assert ctx.matched_setup.name == "breakout_with_momentum"
    assert ctx.matched_setup.n_trades == 0


def test_build_context_no_tech_keeps_matched_setup_none():
    """Without tech, matcher can't run; matched_setup stays None and
    setup_validated fails closed via 'no matched setup'."""
    ctx = build_context(
        _cfg(), _idea(),
        now=datetime(2026, 5, 17), macro=_raw_macro(),
        # tech=None (default)
    )
    assert ctx.matched_setup is None


def test_build_context_explicit_matched_setup_bypasses_matcher():
    """Explicit matched_setup arg wins over matcher (test/fixture path)."""
    from stockbot.strategy.scorer import TechnicalRead

    tech = TechnicalRead(
        trend=0.8, momentum=0.6, breakout=0.7, volume_ok=True,
        rsi=65.0, last=101.0,
        sma20=98.0, sma50=95.0, atr14=2.0,
        high_20d=99.0, volume_last=1_500_000, volume_20d_avg=800_000,
        macd_hist_last=0.3,
    )
    forced = MatchedSetup(
        name="manually_specified", direction="long",
        n_trades=99, expectancy=0.99,
        last_validated_at=datetime(2026, 5, 1),
    )
    ctx = build_context(
        _cfg(), _idea(),
        now=datetime(2026, 5, 17), macro=_raw_macro(),
        tech=tech, matched_setup=forced,
    )
    assert ctx.matched_setup is forced
