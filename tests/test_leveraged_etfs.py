from stockbot.strategy import leveraged_etfs as letfs


def test_registry_has_core_products():
    for sym in ["SPY", "SH", "SDS", "SPXU", "UPRO", "QQQ", "SQQQ", "TQQQ", "SOXL", "SOXS"]:
        assert letfs.is_registered(sym), f"{sym} missing from registry"


def test_effective_leverage_signs():
    assert letfs.effective_leverage("SPY") == 1.0
    assert letfs.effective_leverage("SH") == -1.0
    assert letfs.effective_leverage("SDS") == -2.0
    assert letfs.effective_leverage("SPXU") == -3.0
    assert letfs.effective_leverage("TQQQ") == 3.0
    assert letfs.effective_leverage("SQQQ") == -3.0
    # Unknown symbols default to +1x.
    assert letfs.effective_leverage("UNKNOWN_ETF_XYZ") == 1.0


def test_find_alternatives_bearish_spy():
    alts = letfs.find_alternatives("SPY", "bear")
    syms = [a.symbol for a in alts]
    assert "SH" in syms
    assert "SDS" in syms
    assert "SPXU" in syms
    # Sorted by ascending |leverage| — 1x first, 3x last.
    assert alts[0].leverage == -1.0
    assert alts[-1].leverage == -3.0


def test_find_alternatives_bullish_qqq():
    alts = letfs.find_alternatives("QQQ", "bull")
    syms = [a.symbol for a in alts]
    assert "QLD" in syms and "TQQQ" in syms
    assert alts[-1].symbol == "TQQQ"  # highest leverage last


def test_find_alternatives_max_leverage_caps():
    alts = letfs.find_alternatives("SPY", "bear", max_leverage=1.0)
    # Only the 1x inverse survives.
    assert [a.leverage for a in alts] == [-1.0]


def test_find_alternatives_sector_proxy_nvda_to_semis():
    # NVDA isn't an ETF but maps to SOXX. A bearish NVDA view should propose SOXS.
    alts = letfs.find_alternatives("NVDA", "bear")
    syms = {a.symbol for a in alts}
    assert "SOXS" in syms


def test_decay_warning_thresholds():
    # No warning for 1x.
    assert letfs.decay_warning(1.0, 100) is None
    # 3x for 1 day is fine; 3x for 6 days fires.
    assert letfs.decay_warning(3.0, 1) is None
    assert letfs.decay_warning(3.0, 6) is not None
    # 2x has a longer leash (10 days) but fires past that.
    assert letfs.decay_warning(2.0, 5) is None
    assert letfs.decay_warning(2.0, 15) is not None
    # Inverse leverage uses |leverage|.
    assert letfs.decay_warning(-3.0, 10) is not None


def test_gross_leverage_calculation():
    positions = [
        {"ticker": "SPY",  "weight": 0.50},   # 0.50 * 1 = 0.50
        {"ticker": "TQQQ", "weight": 0.10},   # 0.10 * 3 = 0.30
        {"ticker": "SQQQ", "weight": 0.05},   # 0.05 * 3 = 0.15
    ]
    g = letfs.gross_leverage(positions)
    assert abs(g - (0.50 + 0.30 + 0.15)) < 1e-9


def test_direction_property_matches_sign():
    spxu = letfs.get("SPXU")
    upro = letfs.get("UPRO")
    assert spxu.direction == "bear" and spxu.is_inverse and spxu.is_leveraged
    assert upro.direction == "bull" and not upro.is_inverse and upro.is_leveraged


def test_invalid_direction_raises():
    import pytest
    with pytest.raises(ValueError):
        letfs.find_alternatives("SPY", "sideways")
