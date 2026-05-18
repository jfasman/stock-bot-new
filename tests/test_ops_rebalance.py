"""Tests for ops/rebalance.py — record / approve / reject / list.

Mirrors the pattern of test_conviction_log.py: assume tmp_path SQLite isolation
from conftest.py and exercise the round-trip.
"""
from __future__ import annotations

from stockbot.ops import rebalance as ops_rebalance
from stockbot.portfolio.rebalancer import (
    BookPosition,
    CandidatePick,
    RebalanceConfig,
    rebalance,
)


def _sample_proposal():
    picks = [CandidatePick("A", 0.0), CandidatePick("B", 0.0)]
    cfg = RebalanceConfig(max_turnover_per_rebalance=1.0)
    vols = {"A": 0.20, "B": 0.20}
    return rebalance([], picks, cfg, vols=vols)


def test_record_round_trip():
    p = _sample_proposal()
    pid = ops_rebalance.record(p, config_hash="abc123")
    assert pid > 0
    row = ops_rebalance.get(pid)
    assert row is not None
    assert row["status"] == "pending"
    assert row["algo"] == "equal_risk_contribution"
    assert row["config_hash"] == "abc123"
    # JSON-decoded fields surface alongside raw _json columns.
    assert isinstance(row["changes"], list)
    assert len(row["changes"]) == 2


def test_approve_flips_status():
    pid = ops_rebalance.record(_sample_proposal())
    assert ops_rebalance.approve(pid) is True
    row = ops_rebalance.get(pid)
    assert row["status"] == "approved"
    assert row["decided_at"] is not None
    assert row["decided_by"] == "user"


def test_reject_flips_status():
    pid = ops_rebalance.record(_sample_proposal())
    assert ops_rebalance.reject(pid) is True
    assert ops_rebalance.get(pid)["status"] == "rejected"


def test_decided_proposals_cant_be_re_decided():
    """Once decided, neither approve nor reject should mutate."""
    pid = ops_rebalance.record(_sample_proposal())
    ops_rebalance.approve(pid)
    # Second approve is a no-op.
    assert ops_rebalance.approve(pid) is False
    assert ops_rebalance.reject(pid) is False
    assert ops_rebalance.get(pid)["status"] == "approved"


def test_pending_lists_only_pending():
    p1 = ops_rebalance.record(_sample_proposal())
    p2 = ops_rebalance.record(_sample_proposal())
    p3 = ops_rebalance.record(_sample_proposal())
    ops_rebalance.approve(p1)
    ops_rebalance.reject(p2)
    pend = ops_rebalance.pending()
    ids = [r["id"] for r in pend]
    assert p3 in ids
    assert p1 not in ids
    assert p2 not in ids


def test_recent_includes_all_statuses_newest_first():
    p1 = ops_rebalance.record(_sample_proposal())
    p2 = ops_rebalance.record(_sample_proposal())
    ops_rebalance.approve(p1)
    rows = ops_rebalance.recent(limit=10)
    ids = [r["id"] for r in rows]
    # p2 was inserted last → first in DESC order. p1 still appears (approved).
    assert ids[0] == p2
    assert p1 in ids


def test_infeasible_proposal_still_records():
    """The audit captures *attempts*, not just feasible ones."""
    from stockbot.portfolio.constraints import ConstraintConfig
    picks = [CandidatePick(f"T{i}", 0.0, sector="Tech") for i in range(5)]
    cfg = RebalanceConfig(max_turnover_per_rebalance=1.0)
    constraints = ConstraintConfig(max_position_pct=0.30, max_sector_pct=0.30)
    proposal = rebalance([], picks, cfg, vols=None, constraints=constraints)
    assert proposal.feasible is False
    pid = ops_rebalance.record(proposal)
    row = ops_rebalance.get(pid)
    assert row["feasible"] == 0
    assert len(row["breaches"]) > 0
