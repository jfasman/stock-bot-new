"""Tests for portfolio/rebalancer.py — pure book-level allocator.

Coverage: the three algos, the position cap with redistribution, the
turnover cap, the action labeling on WeightChange, and the constraint-check
integration.
"""
from __future__ import annotations

import math

from stockbot.portfolio.constraints import ConstraintConfig
from stockbot.portfolio.rebalancer import (
    BookPosition,
    CandidatePick,
    RebalanceConfig,
    WeightChange,
    make_backtest_hook,
    rebalance,
)


# -- WeightChange.action labels --------------------------------------------

def test_weight_change_action_labels():
    assert WeightChange("A", 0.0, 0.05).action == "open"
    assert WeightChange("A", 0.05, 0.0).action == "close"
    assert WeightChange("A", 0.04, 0.06).action == "grow"
    assert WeightChange("A", 0.06, 0.04).action == "shrink"
    assert WeightChange("A", 0.05, 0.05).action == "hold"


# -- equal_risk_contribution (default) -------------------------------------

def test_erc_inverse_vol_for_picks_only():
    """No book yet — first allocation, two picks with different vols."""
    picks = [CandidatePick("A", target_weight=0.0), CandidatePick("B", target_weight=0.0)]
    cfg = RebalanceConfig(algo="equal_risk_contribution", max_turnover_per_rebalance=1.0)
    vols = {"A": 0.20, "B": 0.40}
    prop = rebalance([], picks, cfg, vols=vols)
    weights = {c.ticker: c.target_weight for c in prop.changes if abs(c.target_weight) > 0}
    # 1/0.20 : 1/0.40 = 2 : 1 → A=2/3, B=1/3
    assert weights["A"] == pytest_close(2 / 3)
    assert weights["B"] == pytest_close(1 / 3)


def test_erc_combines_book_and_picks():
    book = [BookPosition("A", weight=0.30)]
    picks = [CandidatePick("B", 0.0), CandidatePick("C", 0.0)]
    cfg = RebalanceConfig(max_turnover_per_rebalance=1.0)
    vols = {"A": 0.20, "B": 0.20, "C": 0.20}
    prop = rebalance(book, picks, cfg, vols=vols)
    weights = {c.ticker: c.target_weight for c in prop.changes if c.target_weight > 0}
    # All equal vol → equal weights of 1/3 each. A is in book + picks (treated once).
    assert weights["A"] == pytest_close(1 / 3)
    assert weights["B"] == pytest_close(1 / 3)
    assert weights["C"] == pytest_close(1 / 3)


def test_erc_missing_vols_uses_mean_fallback():
    """A name without a vol entry should not get a degenerate (zero or infinite)
    weight — it should fall back to the mean of the known vols."""
    book = []
    picks = [CandidatePick(t, 0.0) for t in ("A", "B", "C")]
    cfg = RebalanceConfig(max_turnover_per_rebalance=1.0)
    vols = {"A": 0.20, "B": 0.20}  # C missing
    prop = rebalance(book, picks, cfg, vols=vols)
    weights = {c.ticker: c.target_weight for c in prop.changes if c.target_weight > 0}
    assert weights["A"] == pytest_close(1 / 3)
    assert weights["B"] == pytest_close(1 / 3)
    assert weights["C"] == pytest_close(1 / 3)


# -- vol_target_book ---------------------------------------------------------

def test_vol_target_scales_down_when_book_too_hot():
    """Target 10% vol on a book whose inverse-vol allocation would yield ~20%
    vol → the scale factor should knock weights down."""
    picks = [CandidatePick("A", 0.0), CandidatePick("B", 0.0)]
    cfg = RebalanceConfig(algo="vol_target_book", target_vol=0.10, max_turnover_per_rebalance=1.0)
    vols = {"A": 0.30, "B": 0.30}  # high vol → book vol > 0.10
    prop = rebalance([], picks, cfg, vols=vols)
    total = sum(c.target_weight for c in prop.changes if c.target_weight > 0)
    assert total < 1.0  # scaled down — rest stays in cash
    # Book vol of scaled weights should be ≈ target_vol (within ~10% slack).
    weights = {c.ticker: c.target_weight for c in prop.changes if c.target_weight > 0}
    book_vol = math.sqrt(sum((weights[t] * vols[t]) ** 2 for t in weights))
    assert abs(book_vol - 0.10) < 0.01


def test_vol_target_doesnt_scale_above_one():
    """Even if target_vol is huge, weights can't sum above 1 (no leverage)."""
    picks = [CandidatePick("A", 0.0)]
    cfg = RebalanceConfig(algo="vol_target_book", target_vol=10.0, max_turnover_per_rebalance=1.0)
    vols = {"A": 0.20}
    prop = rebalance([], picks, cfg, vols=vols)
    total = sum(c.target_weight for c in prop.changes if c.target_weight > 0)
    assert total <= 1.0 + 1e-9


# -- mean_variance_with_views (BL-lite) ------------------------------------

def test_mv_views_favors_high_midpoint_tight_band():
    """A pick with band (0.08, 0.12) (μ=0.10, tight) should outweigh one with
    band (-0.05, 0.15) (μ=0.05, wide) even at equal vol."""
    picks = [
        CandidatePick("HIGH", 0.0, confidence_band=(0.08, 0.12)),
        CandidatePick("WIDE", 0.0, confidence_band=(-0.05, 0.15)),
    ]
    cfg = RebalanceConfig(algo="mean_variance_with_views", max_turnover_per_rebalance=1.0)
    vols = {"HIGH": 0.20, "WIDE": 0.20}
    prop = rebalance([], picks, cfg, vols=vols)
    weights = {c.ticker: c.target_weight for c in prop.changes if c.target_weight > 0}
    assert weights["HIGH"] > weights["WIDE"]


def test_mv_views_no_bands_falls_back_to_inverse_vol():
    picks = [CandidatePick("A", 0.0), CandidatePick("B", 0.0)]
    cfg = RebalanceConfig(algo="mean_variance_with_views", max_turnover_per_rebalance=1.0)
    vols = {"A": 0.20, "B": 0.40}
    prop = rebalance([], picks, cfg, vols=vols)
    weights = {c.ticker: c.target_weight for c in prop.changes if c.target_weight > 0}
    # Same inverse-vol math as ERC.
    assert weights["A"] == pytest_close(2 / 3)
    assert weights["B"] == pytest_close(1 / 3)
    assert any("reduced to inverse-vol" in n for n in prop.notes)


def test_mv_views_negative_midpoint_doesnt_get_negative_weight():
    """Long-only book: a pick with μ < 0 should get zero weight, not negative."""
    picks = [
        CandidatePick("BAD", 0.0, confidence_band=(-0.20, -0.10)),
        CandidatePick("GOOD", 0.0, confidence_band=(0.05, 0.15)),
    ]
    cfg = RebalanceConfig(algo="mean_variance_with_views", max_turnover_per_rebalance=1.0)
    vols = {"BAD": 0.20, "GOOD": 0.20}
    prop = rebalance([], picks, cfg, vols=vols)
    weights = {c.ticker: c.target_weight for c in prop.changes}
    assert weights.get("BAD", 0.0) <= 1e-9
    assert weights["GOOD"] > 0


# -- position cap ---------------------------------------------------------

def test_position_cap_redistributes_excess():
    """One name's natural weight exceeds the cap → cap and redistribute."""
    picks = [CandidatePick(t, 0.0) for t in ("BIG", "A", "B", "C", "D", "E")]
    cfg = RebalanceConfig(max_turnover_per_rebalance=1.0)
    # BIG natural weight ≈ 1/(0.10) / (1/0.10 + 5*(1/0.30)) = 10 / (10 + 16.67) ≈ 0.375.
    # Others get the remaining 0.625 split equally → ~0.125 each.
    # Cap = 0.20 → BIG capped, excess 0.175 redistributes to others, no chain hit.
    vols = {"BIG": 0.10, "A": 0.30, "B": 0.30, "C": 0.30, "D": 0.30, "E": 0.30}
    constraints = ConstraintConfig(max_position_pct=0.20)
    prop = rebalance([], picks, cfg, vols=vols, constraints=constraints)
    weights = {c.ticker: c.target_weight for c in prop.changes if c.target_weight > 0}
    assert weights["BIG"] == pytest_close(0.20)
    # Sum stays at 1.0 — no chain-cap (others still under 0.20 after redistribution).
    assert sum(weights.values()) == pytest_close(1.0)


def test_position_cap_partial_fill_when_cap_binds_everywhere():
    """When the cap × n_names < 1.0, the book can only fill to cap × n.
    The remainder stays as cash — not a bug; document that behavior."""
    picks = [CandidatePick(t, 0.0) for t in ("A", "B", "C", "D")]
    cfg = RebalanceConfig(max_turnover_per_rebalance=1.0)
    vols = {t: 0.20 for t in ("A", "B", "C", "D")}  # equal vol → equal natural weights 0.25
    constraints = ConstraintConfig(max_position_pct=0.20)  # 4 × 0.20 = 0.80 ceiling
    prop = rebalance([], picks, cfg, vols=vols, constraints=constraints)
    weights = {c.ticker: c.target_weight for c in prop.changes if c.target_weight > 0}
    assert sum(weights.values()) == pytest_close(0.80)
    for t in ("A", "B", "C", "D"):
        assert weights[t] == pytest_close(0.20)


# -- turnover cap ---------------------------------------------------------

def test_turnover_cap_scales_toward_current():
    """Current book is 100% in A; target says A=0, B=1. Raw turnover is 2.0.
    With max_turnover=0.40, the proposal should land somewhere between."""
    book = [BookPosition("A", weight=1.0)]
    picks = [CandidatePick("B", 0.0)]
    cfg = RebalanceConfig(max_turnover_per_rebalance=0.40)
    vols = {"A": 0.20, "B": 0.20}
    # NOTE: with both A and B in universe, inverse-vol → A=B=0.5 → raw turnover = 1.0
    prop = rebalance(book, picks, cfg, vols=vols)
    assert prop.raw_turnover == pytest_close(1.0)  # |0.5-1.0| + |0.5-0| = 0.5+0.5
    assert prop.capped_turnover <= 0.40 + 1e-9
    # After capping: alpha = 0.4 → A = 1 + 0.4*(0.5-1) = 0.8, B = 0 + 0.4*(0.5-0) = 0.2
    weights = {c.ticker: c.target_weight for c in prop.changes}
    assert weights["A"] == pytest_close(0.80)
    assert weights["B"] == pytest_close(0.20)


def test_no_turnover_cap_below_threshold():
    book = [BookPosition("A", weight=0.5)]
    picks = [CandidatePick("B", 0.0)]
    cfg = RebalanceConfig(max_turnover_per_rebalance=1.0)
    vols = {"A": 0.20, "B": 0.20}
    prop = rebalance(book, picks, cfg, vols=vols)
    assert prop.raw_turnover == prop.capped_turnover


# -- constraint feasibility ---------------------------------------------

def test_proposal_flagged_infeasible_on_sector_breach():
    """All picks in same sector → sector weight exceeds limit → infeasible."""
    picks = [
        CandidatePick(f"T{i}", 0.0, sector="Technology") for i in range(5)
    ]
    cfg = RebalanceConfig(max_turnover_per_rebalance=1.0)
    constraints = ConstraintConfig(max_position_pct=0.30, max_sector_pct=0.30)
    prop = rebalance([], picks, cfg, vols=None, constraints=constraints)
    # Each gets weight 0.2 → sector total 1.0 > 0.30.
    assert prop.feasible is False
    assert any("sector Technology" in b for b in prop.constraint_breaches)
    # The proposal is still returned (audit captures attempts).
    assert len(prop.changes) > 0


def test_proposal_feasible_when_constraints_respected():
    picks = [CandidatePick(f"T{i}", 0.0, sector=f"S{i}") for i in range(5)]
    cfg = RebalanceConfig(max_turnover_per_rebalance=1.0)
    constraints = ConstraintConfig(max_position_pct=0.30, max_sector_pct=0.30)
    prop = rebalance([], picks, cfg, vols=None, constraints=constraints)
    assert prop.feasible is True
    assert prop.constraint_breaches == []


# -- empty / edge cases -------------------------------------------------

def test_empty_book_and_picks():
    prop = rebalance([], [], RebalanceConfig(), vols=None)
    assert prop.changes == []
    assert prop.raw_turnover == 0.0
    assert prop.capped_turnover == 0.0
    assert prop.feasible is True


def test_unknown_algo_falls_back_to_erc_with_note():
    """Typo in config shouldn't crash the engine cycle."""
    picks = [CandidatePick("A", 0.0), CandidatePick("B", 0.0)]
    cfg = RebalanceConfig(algo="nonsense", max_turnover_per_rebalance=1.0)
    vols = {"A": 0.20, "B": 0.20}
    prop = rebalance([], picks, cfg, vols=vols)
    weights = {c.ticker: c.target_weight for c in prop.changes if c.target_weight > 0}
    assert weights["A"] == pytest_close(0.50)
    assert weights["B"] == pytest_close(0.50)
    assert any("Unknown algo" in n for n in prop.notes)


def test_proposal_separates_grows_and_shrinks():
    book = [BookPosition("HOLD", 0.30)]
    picks = [CandidatePick("HOLD", 0.0), CandidatePick("NEW", 0.0)]
    cfg = RebalanceConfig(max_turnover_per_rebalance=1.0)
    vols = {"HOLD": 0.20, "NEW": 0.20}
    prop = rebalance(book, picks, cfg, vols=vols)
    # ERC pushes both to 0.5 — HOLD grows from 0.30 to 0.50, NEW opens at 0.50.
    actions = {c.ticker: c.action for c in prop.changes}
    assert actions["HOLD"] == "grow"
    assert actions["NEW"] == "open"
    assert len(prop.grows) == 2
    assert prop.shrinks == []


# -- backtest hook adapter ----------------------------------------------

def test_backtest_hook_returns_weights_in_signature_the_engine_expects():
    """make_backtest_hook produces a (current, signal) → adjusted closure."""
    hook = make_backtest_hook(RebalanceConfig(max_turnover_per_rebalance=1.0))
    # Empty current, two signal tickers → equal-weight (no vol info → fallback).
    adjusted = hook({}, {"A": 0.0, "B": 0.0})
    assert set(adjusted.keys()) == {"A", "B"}
    assert adjusted["A"] == pytest_close(0.5)
    assert adjusted["B"] == pytest_close(0.5)


def test_backtest_hook_uses_vol_loader_when_supplied():
    vols = {"A": 0.10, "B": 0.40}
    hook = make_backtest_hook(
        RebalanceConfig(max_turnover_per_rebalance=1.0),
        vol_loader=lambda t: vols.get(t),
    )
    adjusted = hook({}, {"A": 0.0, "B": 0.0})
    # 1/0.10 : 1/0.40 = 4 : 1 → A=0.8, B=0.2.
    assert adjusted["A"] == pytest_close(0.8)
    assert adjusted["B"] == pytest_close(0.2)


# -- tiny helper ---------------------------------------------------------

def pytest_close(expected: float, rel: float = 1e-6):
    """Mimic pytest.approx so the test file can stay import-free in this list."""
    class _Close:
        def __eq__(self, other):
            return abs(float(other) - expected) <= rel + abs(expected) * rel
        def __repr__(self):
            return f"≈{expected}"
    return _Close()
