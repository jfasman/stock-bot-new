"""Tests for strategy/factors/universe.py — peer assembly + min-universe gating.

Roadmap §13.6: the universe is the seam between "what we're scoring" and "what
we rank against." None means "fall back to the two-way blend."
"""
from stockbot.data.fundamentals import Fundamentals
from stockbot.strategy.factors.universe import build_universe, resolve_peer_pool


def _fund(ticker: str, sector: str | None) -> Fundamentals:
    return Fundamentals(ticker=ticker, sector=sector)


def test_returns_none_when_below_min_universe():
    """Single target with no peers and no watchlist → universe of 1 → None."""
    u = build_universe("AAPL", min_universe=12)
    assert u is None


def test_assembles_watchlist_plus_peer_pool():
    watchlist = [f"W{i}" for i in range(5)]
    peers = [f"P{i}" for i in range(10)]
    u = build_universe("AAPL", watchlist=watchlist, peer_pool=peers, min_universe=12)
    assert u is not None
    assert u[0] == "AAPL"
    assert set(u) == {"AAPL", *watchlist, *peers}


def test_dedups_target_already_in_watchlist():
    watchlist = ["AAPL", "MSFT", "GOOG"]
    peers = [f"P{i}" for i in range(10)]
    u = build_universe("AAPL", watchlist=watchlist, peer_pool=peers, min_universe=12)
    assert u is not None
    assert u.count("AAPL") == 1
    # Target stays at index 0; watchlist preserves insertion order otherwise.
    assert u[0] == "AAPL"


def test_uppercases_inputs():
    u = build_universe(
        "aapl",
        watchlist=["msft", "goog"],
        peer_pool=[f"p{i}" for i in range(15)],
        min_universe=10,
    )
    assert u is not None
    assert all(t == t.upper() for t in u)


def test_sector_match_filters_peers_to_target_sector():
    """When fundamentals indicate the target is Tech, only Tech peers join."""
    funds = {
        "AAPL": _fund("AAPL", "Technology"),
        "MSFT": _fund("MSFT", "Technology"),
        "JPM": _fund("JPM", "Financials"),
        "XOM": _fund("XOM", "Energy"),
        "NVDA": _fund("NVDA", "Technology"),
        "BAC": _fund("BAC", "Financials"),
    }
    u = build_universe(
        "AAPL",
        peer_pool=["MSFT", "JPM", "XOM", "NVDA", "BAC"],
        fundamentals=funds,
        min_universe=3,
        sector_match=True,
    )
    assert u is not None
    assert "JPM" not in u and "XOM" not in u and "BAC" not in u
    assert "MSFT" in u and "NVDA" in u


def test_sector_match_disabled_keeps_all_peers():
    funds = {"AAPL": _fund("AAPL", "Technology"), "JPM": _fund("JPM", "Financials")}
    u = build_universe(
        "AAPL",
        peer_pool=["JPM"] + [f"P{i}" for i in range(15)],
        fundamentals=funds,
        min_universe=2,
        sector_match=False,
    )
    assert u is not None
    assert "JPM" in u


def test_unknown_target_sector_falls_back_to_unfiltered_pool():
    """No fundamentals row for target → can't infer sector → include all peers."""
    funds = {"JPM": _fund("JPM", "Financials"), "XOM": _fund("XOM", "Energy")}
    u = build_universe(
        "UNKNOWN",
        peer_pool=["JPM", "XOM"] + [f"P{i}" for i in range(10)],
        fundamentals=funds,
        min_universe=3,
        sector_match=True,
    )
    assert u is not None
    assert "JPM" in u and "XOM" in u  # not filtered out — sector unknown


def test_watchlist_included_regardless_of_sector():
    """Watchlist is the user's curated set — included entire, even if cross-sector."""
    funds = {
        "AAPL": _fund("AAPL", "Technology"),
        "JPM": _fund("JPM", "Financials"),
    }
    u = build_universe(
        "AAPL",
        watchlist=["JPM"] + [f"W{i}" for i in range(12)],
        fundamentals=funds,
        min_universe=3,
        sector_match=True,
    )
    assert u is not None
    assert "JPM" in u  # in watchlist despite cross-sector


def test_multi_target_uses_union_of_sectors():
    funds = {
        "AAPL": _fund("AAPL", "Technology"),
        "JPM": _fund("JPM", "Financials"),
        "MSFT": _fund("MSFT", "Technology"),
        "BAC": _fund("BAC", "Financials"),
        "XOM": _fund("XOM", "Energy"),
    }
    u = build_universe(
        ["AAPL", "JPM"],
        peer_pool=["MSFT", "BAC", "XOM"],
        fundamentals=funds,
        min_universe=3,
        sector_match=True,
    )
    assert u is not None
    assert "MSFT" in u and "BAC" in u  # both sectors included
    assert "XOM" not in u  # Energy peer dropped


def test_resolve_peer_pool_passes_through_literals():
    pool = resolve_peer_pool(["AAPL", "msft", "JPM"])
    assert pool == ["AAPL", "MSFT", "JPM"]


def test_resolve_peer_pool_dedups():
    assert resolve_peer_pool(["AAPL", "aapl", "MSFT"]) == ["AAPL", "MSFT"]


def test_resolve_peer_pool_named_token_is_placeholder():
    """SPY_constituents is a placeholder until a constituents file ships."""
    pool = resolve_peer_pool(["AAPL", "SPY_constituents", "MSFT"])
    assert pool == ["AAPL", "MSFT"]  # named token expands to nothing for now
