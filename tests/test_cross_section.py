from stockbot.strategy.cross_section import rank_pct, sector_neutralize, zscore


def test_rank_pct_higher_is_better():
    out = rank_pct({"A": 1, "B": 2, "C": 3}, higher_is_better=True)
    assert out["C"] > out["A"]
    assert -1 <= out["A"] <= 1
    assert -1 <= out["C"] <= 1


def test_rank_pct_inverts_when_lower_is_better():
    out = rank_pct({"A": 10, "B": 20, "C": 30}, higher_is_better=False)
    assert out["A"] > out["C"]


def test_rank_pct_missing_maps_to_zero():
    out = rank_pct({"A": 1, "B": None, "C": 3})
    assert out["B"] == 0.0


def test_zscore_clips():
    out = zscore({"A": 0, "B": 0, "C": 1000}, clip=3.0)
    assert abs(out["C"]) <= 3.0


def test_sector_neutralize_removes_sector_mean():
    scores = {"A": 1.0, "B": 1.0, "C": -1.0, "D": -1.0}
    sectors = {"A": "tech", "B": "tech", "C": "bank", "D": "bank"}
    out = sector_neutralize(scores, sectors)
    assert abs(out["A"]) < 1e-9
    assert abs(out["C"]) < 1e-9
