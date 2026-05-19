"""Integration test for engine.paper.run_rebalance — proposes + records."""
from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pandas as pd

from stockbot.config import Config
from stockbot.engine.paper import run_rebalance
from stockbot.ops import rebalance as ops_rebalance
from stockbot.portfolio.portfolio import Portfolio


def _cfg(*, enabled: bool = True) -> Config:
    return Config(raw={
        "portfolio": {"starting_cash": 100_000, "max_position_pct": 0.10},
        "risk": {},
        "strategy": {
            "sentiment_weight": 0.4, "factor_weight": 0.0,
            "min_score_to_trade": 0.55, "bear_score_to_trade": -0.55,
            "rsi_oversold": 30, "rsi_overbought": 70, "min_avg_volume": 0,
        },
        "sentiment": {},
        "rebalance": {
            "enabled": enabled,
            "algo": "equal_risk_contribution",
            "max_turnover_per_rebalance": 0.50,
        },
        "watchlist": ["A", "B"],
        "factors": {},
        "leveraged_etfs": {"enabled": False},
    })


def _uptrend_history(days: int = 200) -> pd.DataFrame:
    close = pd.Series(np.linspace(100, 200, days))
    idx = pd.date_range("2024-01-02", periods=days, freq="B")
    return pd.DataFrame(
        {"Open": close, "High": close * 1.01, "Low": close * 0.99,
         "Close": close, "Volume": [1_000_000] * days},
        index=idx,
    )


def test_run_rebalance_skips_when_disabled():
    cfg = _cfg(enabled=False)
    portfolio = Portfolio(starting_cash=100_000)
    assert run_rebalance(cfg, portfolio, ["A", "B"]) is None


def test_run_rebalance_records_proposal_with_picks():
    """With existing positions in the book, run_rebalance should compute
    weights and record a pending proposal — independent of whether
    generate_ideas surfaces new candidates."""
    from datetime import datetime

    from stockbot.data.fundamentals import Fundamentals
    from stockbot.strategy.ideas import Idea

    cfg = _cfg(enabled=True)
    portfolio = Portfolio(starting_cash=100_000)
    # Seed a real position so the rebalancer has a book to operate on.
    portfolio.open_position(
        ticker="A", instrument="equity", direction="long",
        quantity=100, entry_price=50.0,
    )

    history = {"A": _uptrend_history(), "B": _uptrend_history()}

    def get_history(t, *args, **kwargs):
        return history.get(t.upper(), pd.DataFrame())

    seeded_idea = Idea(
        ticker="B", direction="long", instrument="equity", score=0.7,
        last_price=150.0, rsi=55.0, reasons=["seeded"],
        sentiment_net=0.0, sentiment_confidence=0.0,
    )

    with patch("stockbot.engine.paper.price_data.get_history", side_effect=get_history), \
         patch("stockbot.engine.paper.generate_ideas", return_value=[seeded_idea]), \
         patch("stockbot.data.fundamentals.get_fundamentals",
               return_value=Fundamentals(ticker="X", sector="Tech", beta=1.0)):
        pid = run_rebalance(cfg, portfolio, ["A", "B"])

    assert pid is not None
    row = ops_rebalance.get(pid)
    assert row is not None
    assert row["status"] == "pending"
    assert row["algo"] == "equal_risk_contribution"
    # Book had A; new pick was B → proposal touches both.
    tickers_in_changes = {c["ticker"] for c in row["changes"]}
    assert "A" in tickers_in_changes
    assert "B" in tickers_in_changes


def test_run_rebalance_returns_none_when_nothing_to_rebalance():
    """Empty book + no qualifying picks → nothing to propose."""
    cfg = _cfg(enabled=True)
    portfolio = Portfolio(starting_cash=100_000)

    # No price history → ideas will return [] → nothing to rebalance.
    def empty_history(t, *args, **kwargs):
        return pd.DataFrame()

    with patch("stockbot.engine.paper.price_data.get_history", side_effect=empty_history), \
         patch("stockbot.strategy.ideas.price_data.get_history", side_effect=empty_history), \
         patch("stockbot.strategy.ideas.aggregate", return_value={}):
        pid = run_rebalance(cfg, portfolio, ["A"])

    assert pid is None


# -- execute_rebalance — roadmap §13.8 execute step --------------------------

def test_execute_rebalance_refuses_non_approved():
    """Pending or rejected proposals must not be executable."""
    from stockbot.engine.paper import execute_rebalance
    from stockbot.portfolio.rebalancer import CandidatePick, RebalanceConfig, rebalance

    cfg = _cfg(enabled=True)
    portfolio = Portfolio(starting_cash=100_000)
    # Manually build + record a proposal in pending state.
    proposal = rebalance(
        [], [CandidatePick("A", 0.0), CandidatePick("B", 0.0)],
        RebalanceConfig(max_turnover_per_rebalance=1.0),
        vols={"A": 0.2, "B": 0.2},
    )
    pid = ops_rebalance.record(proposal)
    # Status is 'pending' — executor must refuse.
    result = execute_rebalance(cfg, portfolio, pid, ["A", "B"])
    assert result is None


def test_execute_rebalance_returns_none_for_unknown_id():
    from stockbot.engine.paper import execute_rebalance
    cfg = _cfg(enabled=True)
    portfolio = Portfolio(starting_cash=100_000)
    assert execute_rebalance(cfg, portfolio, 99999, ["A"]) is None


def test_execute_rebalance_closes_marked_positions_without_gate():
    """A 'close' change (target=0) must close the position regardless of gate.

    We construct the RebalanceProposal directly so we can guarantee a close
    edge — the pure ERC math doesn't naturally produce target=0 on a
    single-name book (that's tested separately in test_rebalancer.py).
    """
    from stockbot.engine.paper import execute_rebalance
    from stockbot.portfolio.rebalancer import RebalanceProposal, WeightChange

    cfg = _cfg(enabled=True)
    portfolio = Portfolio(starting_cash=100_000)
    pos_id = portfolio.open_position(
        ticker="DROPME", instrument="equity", direction="long",
        quantity=50, entry_price=20.0,
    )
    proposal = RebalanceProposal(
        algo="equal_risk_contribution",
        changes=[WeightChange(ticker="DROPME", current_weight=0.10, target_weight=0.0)],
        raw_turnover=0.10, capped_turnover=0.10,
        feasible=True,
    )
    pid = ops_rebalance.record(proposal)
    ops_rebalance.approve(pid)

    # Provide a mark price so close has something to settle against.
    with patch("stockbot.engine.paper._mark_price", return_value=22.0):
        result = execute_rebalance(cfg, portfolio, pid, [])

    assert result is not None
    assert any("DROPME" in line for line in result.closed)
    # Position is closed in the portfolio.
    assert all(p.id != pos_id for p in portfolio.list_open())
    # Proposal flipped to executed.
    assert ops_rebalance.get(pid)["status"] == "executed"


def test_execute_rebalance_filters_grow_when_gate_blocks():
    """A grow whose conviction gate fails must be skipped — and the proposal
    still flips to executed (intent recorded)."""
    from datetime import datetime
    from unittest.mock import MagicMock
    from stockbot.engine.paper import execute_rebalance
    from stockbot.portfolio.rebalancer import CandidatePick, RebalanceConfig, rebalance
    from stockbot.strategy.conviction import GateResult
    from stockbot.strategy.ideas import Idea

    cfg = _cfg(enabled=True)
    portfolio = Portfolio(starting_cash=100_000)

    # Build an approved open-NEWBIE proposal.
    proposal = rebalance(
        [],
        [CandidatePick("NEWBIE", 0.0), CandidatePick("OTHER", 0.0)],
        RebalanceConfig(max_turnover_per_rebalance=1.0),
        vols={"NEWBIE": 0.2, "OTHER": 0.2},
    )
    pid = ops_rebalance.record(proposal)
    ops_rebalance.approve(pid)

    seeded_idea_newbie = Idea(
        ticker="NEWBIE", direction="long", instrument="equity", score=0.7,
        last_price=100.0, rsi=55.0, reasons=[],
        sentiment_net=0.0, sentiment_confidence=0.0,
    )
    seeded_idea_other = Idea(
        ticker="OTHER", direction="long", instrument="equity", score=0.7,
        last_price=50.0, rsi=55.0, reasons=[],
        sentiment_net=0.0, sentiment_confidence=0.0,
    )

    # Gate fails for NEWBIE, passes for OTHER.
    def fake_evaluate(idea, ctx):
        if idea.ticker == "NEWBIE":
            return None, {"score": GateResult(passed=False, reason="too low")}
        # OTHER passes — return a non-None pick (any truthy object).
        return MagicMock(), {"score": GateResult(passed=True, reason="ok")}

    with patch("stockbot.engine.paper.generate_ideas",
               return_value=[seeded_idea_newbie, seeded_idea_other]), \
         patch("stockbot.engine.paper.price_data.get_history",
               return_value=_uptrend_history()), \
         patch("stockbot.strategy.scorer.read_technicals", return_value=MagicMock()), \
         patch("stockbot.strategy.conviction_context.build_context", return_value=MagicMock()), \
         patch("stockbot.strategy.conviction.evaluate", side_effect=fake_evaluate), \
         patch("stockbot.data.macro.snapshot", return_value=MagicMock()), \
         patch("stockbot.data.fundamentals.get_fundamentals", return_value=None):
        result = execute_rebalance(cfg, portfolio, pid, ["NEWBIE", "OTHER"])

    assert result is not None
    assert "NEWBIE" in result.gate_filtered
    assert "NEWBIE" not in [line.split()[0] for line in result.opened]
    assert ops_rebalance.get(pid)["status"] == "executed"
