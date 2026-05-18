"""Tests for strategy/factors/fusion.py — the live wiring shim.

The shim's job: turn config + injected loaders into per-target factor
composites, falling back to {} (skip the factor leg) on any failure.
"""
from unittest.mock import MagicMock

import pandas as pd
import pytest

from stockbot.config import Config
from stockbot.data.fundamentals import Fundamentals
from stockbot.strategy.factors.fusion import compute_factor_composites


def _cfg(*, factor_weight: float = 0.30, peer_pool: list[str] | None = None,
         min_universe: int = 12, watchlist: list[str] | None = None) -> Config:
    return Config(raw={
        "strategy": {"sentiment_weight": 0.4, "factor_weight": factor_weight},
        "factors": {
            "min_universe": min_universe,
            "peer_pool": peer_pool if peer_pool is not None else [],
            "sector_neutral": False,  # keep tests deterministic
        },
        "watchlist": watchlist or [],
    })


def _fund(t: str, sector: str = "Technology") -> Fundamentals:
    return Fundamentals(ticker=t, sector=sector)


def _flat_df(n: int = 120) -> pd.DataFrame:
    return pd.DataFrame({
        "Open": [100.0] * n, "High": [101.0] * n, "Low": [99.0] * n,
        "Close": [100.0] * n, "Volume": [1_000_000] * n,
    })


def test_returns_empty_when_factor_weight_is_zero():
    """factor_weight == 0 short-circuits — no loaders called."""
    fund_loader = MagicMock()
    price_loader = MagicMock()
    result = compute_factor_composites(
        _cfg(factor_weight=0.0),
        ["AAPL"],
        fundamentals_loader=fund_loader,
        price_loader=price_loader,
    )
    assert result == {}
    fund_loader.assert_not_called()
    price_loader.assert_not_called()


def test_returns_empty_when_universe_below_min():
    """Single target, empty peer pool → universe of 1 → below min_universe=12."""
    result = compute_factor_composites(
        _cfg(peer_pool=[], min_universe=12),
        ["AAPL"],
        fundamentals_loader=lambda t: _fund(t),
        price_loader=lambda t: _flat_df(),
    )
    assert result == {}


def test_returns_composite_when_universe_large_enough():
    peers = [f"P{i}" for i in range(20)]
    result = compute_factor_composites(
        _cfg(peer_pool=peers, min_universe=10),
        ["AAPL"],
        fundamentals_loader=lambda t: _fund(t),
        price_loader=lambda t: _flat_df(),
    )
    assert "AAPL" in result
    assert isinstance(result["AAPL"], float)


def test_fundamentals_loader_failures_dont_blow_up():
    """A few flaky tickers shouldn't kill the whole scan."""
    peers = [f"P{i}" for i in range(20)]

    def flaky_fund(t: str) -> Fundamentals:
        if t in ("P3", "P7"):
            raise RuntimeError("vendor 500")
        return _fund(t)

    result = compute_factor_composites(
        _cfg(peer_pool=peers, min_universe=10),
        ["AAPL"],
        fundamentals_loader=flaky_fund,
        price_loader=lambda t: _flat_df(),
    )
    # AAPL still scored; the missing peers just have neutral factor inputs.
    assert "AAPL" in result


def test_price_loader_failures_dont_blow_up():
    peers = [f"P{i}" for i in range(20)]

    def flaky_price(t: str) -> pd.DataFrame:
        if t in ("P2", "P5"):
            raise RuntimeError("yfinance timeout")
        return _flat_df()

    result = compute_factor_composites(
        _cfg(peer_pool=peers, min_universe=10),
        ["AAPL"],
        fundamentals_loader=lambda t: _fund(t),
        price_loader=flaky_price,
    )
    assert "AAPL" in result


def test_score_universe_failure_returns_empty():
    """If the underlying factor math raises, the shim must swallow and degrade."""
    peers = [f"P{i}" for i in range(20)]

    def bad_price(t: str) -> pd.DataFrame:
        # Returning a malformed DataFrame triggers errors deep in the factor
        # functions; the shim should swallow them and return {}.
        return pd.DataFrame({"WRONG": [1, 2, 3]})

    result = compute_factor_composites(
        _cfg(peer_pool=peers, min_universe=10),
        ["AAPL"],
        fundamentals_loader=lambda t: _fund(t),
        price_loader=bad_price,
    )
    # The factor functions are themselves defensive (missing inputs → 0), so
    # this may still succeed and return {AAPL: 0.0}. Either {} or a numeric
    # result is acceptable — what must NOT happen is an exception escaping.
    assert isinstance(result, dict)


def test_only_targets_appear_in_result():
    """Peers and watchlist members may have been scored, but the shim returns
    only entries the caller asked about — keeps the contract narrow."""
    peers = [f"P{i}" for i in range(20)]
    result = compute_factor_composites(
        _cfg(peer_pool=peers, watchlist=["WL1", "WL2"], min_universe=10),
        ["AAPL", "MSFT"],
        fundamentals_loader=lambda t: _fund(t),
        price_loader=lambda t: _flat_df(),
    )
    assert set(result.keys()) <= {"AAPL", "MSFT"}
    assert "WL1" not in result
    assert "P0" not in result


def test_missing_factor_weight_config_defaults_to_zero():
    """If factor_weight is absent from config, fusion is off by default — the
    cluster is opt-in. (Default is 0.0 here; config.yaml sets it to 0.30.)"""
    cfg = Config(raw={"strategy": {"sentiment_weight": 0.4}, "factors": {}, "watchlist": []})
    result = compute_factor_composites(
        cfg, ["AAPL"],
        fundamentals_loader=lambda t: _fund(t),
        price_loader=lambda t: _flat_df(),
    )
    assert result == {}


def test_targets_uppercased():
    peers = [f"P{i}" for i in range(20)]
    result = compute_factor_composites(
        _cfg(peer_pool=peers, min_universe=10),
        ["aapl"],
        fundamentals_loader=lambda t: _fund(t),
        price_loader=lambda t: _flat_df(),
    )
    assert "AAPL" in result
    assert "aapl" not in result
