"""Tests for engine/brokers/alpaca.py — read-only Alpaca client.

All HTTP is mocked via an injected session, so no network is touched.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import MagicMock

import pytest

from stockbot.engine.brokers.alpaca import AlpacaLiveBroker


def _mock_response(json_data, status: int = 200):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = json_data
    r.text = str(json_data)
    return r


def _broker(session) -> AlpacaLiveBroker:
    return AlpacaLiveBroker(
        access_token="KEY", secret_key="SECRET",
        paper=True, http_session=session,
    )


def test_account_summary_maps_fields():
    session = MagicMock()
    session.get.return_value = _mock_response({
        "account_number": "ABC123",
        "cash": "1000.50",
        "buying_power": "2000.00",
        "equity": "5000.00",
        "last_equity": "4800.00",
        "currency": "USD",
        "status": "ACTIVE",
        "pattern_day_trader": False,
    })
    summary = _broker(session).account_summary()
    assert summary.account_id == "ABC123"
    assert summary.cash == 1000.50
    assert summary.buying_power == 2000.00
    assert summary.equity == 5000.00
    assert summary.last_equity == 4800.00
    assert summary.pdt is False


def test_account_summary_tolerates_missing_optional_fields():
    """`last_equity` and `pattern_day_trader` are optional — must not raise."""
    session = MagicMock()
    session.get.return_value = _mock_response({
        "account_number": "X", "cash": "0", "buying_power": "0", "equity": "0",
    })
    summary = _broker(session).account_summary()
    assert summary.last_equity is None
    assert summary.pdt is None


def test_positions_maps_each_row():
    session = MagicMock()
    session.get.return_value = _mock_response([
        {
            "symbol": "AAPL", "qty": "100",
            "avg_entry_price": "150.00", "current_price": "175.00",
            "market_value": "17500.00", "unrealized_pl": "2500.00",
            "unrealized_plpc": "0.1667", "side": "long",
        },
        {
            "symbol": "msft", "qty": "50",
            "avg_entry_price": "300.00", "current_price": "320.00",
            "market_value": "16000.00", "unrealized_pl": "1000.00",
            "unrealized_plpc": "0.0667", "side": "long",
        },
    ])
    positions = _broker(session).positions()
    assert len(positions) == 2
    assert positions[0].ticker == "AAPL"
    assert positions[0].quantity == 100
    assert positions[0].market_value == 17500.00
    assert positions[1].ticker == "MSFT"  # uppercased


def test_transactions_parses_fills_and_skips_malformed():
    session = MagicMock()
    session.get.return_value = _mock_response([
        {
            "id": "txn-1", "symbol": "AAPL", "side": "buy",
            "qty": "10", "price": "150.00",
            "transaction_time": "2026-05-15T14:30:00Z",
        },
        # Malformed row: missing price — must NOT crash; just gets skipped.
        {"id": "txn-bad", "symbol": "BAD", "side": "buy", "qty": "5"},
    ])
    txns = _broker(session).transactions(since=date(2026, 5, 1))
    assert len(txns) == 1
    assert txns[0].ticker == "AAPL"
    assert txns[0].quantity == 10
    assert txns[0].price == 150.00
    assert txns[0].transaction_id == "txn-1"


def test_transactions_passes_iso_after_param():
    session = MagicMock()
    session.get.return_value = _mock_response([])
    _broker(session).transactions(since=date(2026, 5, 1))
    _, kwargs = session.get.call_args
    assert "after" in kwargs["params"]
    assert kwargs["params"]["after"].startswith("2026-05-01")


def test_quote_uses_bid_ask_midpoint_when_both_present():
    session = MagicMock()
    session.get.return_value = _mock_response({
        "quote": {"bp": 100.0, "ap": 100.20, "t": "2026-05-18T15:00:00Z"},
    })
    q = _broker(session).quote("AAPL")
    assert q.ticker == "AAPL"
    assert q.bid == 100.0
    assert q.ask == 100.20
    # mid is computed lazily on the property; `last` is also the midpoint when
    # both sides are present.
    assert q.last == pytest.approx(100.10)
    assert q.mid == pytest.approx(100.10)


def test_quote_falls_back_to_single_side_when_only_one_present():
    session = MagicMock()
    session.get.return_value = _mock_response({"quote": {"bp": 99.50}})
    q = _broker(session).quote("AAPL")
    assert q.last == 99.50


def test_http_error_raises():
    session = MagicMock()
    session.get.return_value = _mock_response({"error": "unauthorized"}, status=401)
    with pytest.raises(RuntimeError, match="401"):
        _broker(session).account_summary()


def test_send_auth_headers():
    session = MagicMock()
    session.get.return_value = _mock_response({"cash": "0", "buying_power": "0", "equity": "0"})
    _broker(session).account_summary()
    _, kwargs = session.get.call_args
    assert kwargs["headers"]["APCA-API-KEY-ID"] == "KEY"
    assert kwargs["headers"]["APCA-API-SECRET-KEY"] == "SECRET"


def test_no_order_methods_exposed():
    """The compiler-level read-only guarantee: place_order / submit_order /
    cancel_order are not attributes on a LiveBroker."""
    b = _broker(MagicMock())
    assert not hasattr(b, "place_order")
    assert not hasattr(b, "submit_order")
    assert not hasattr(b, "cancel_order")


def test_satisfies_live_broker_protocol():
    """Runtime check that the impl satisfies the structural Protocol."""
    from stockbot.engine.brokers.base import LiveBroker
    b = _broker(MagicMock())
    assert isinstance(b, LiveBroker)


def test_paper_endpoint_used_by_default():
    """Default paper=True uses the sandbox base URL — important so a misconfigured
    test doesn't ping the live endpoint."""
    b = AlpacaLiveBroker(access_token="K", secret_key="S", http_session=MagicMock())
    assert "paper" in b._base


def test_live_endpoint_when_paper_false():
    b = AlpacaLiveBroker(access_token="K", secret_key="S", paper=False, http_session=MagicMock())
    assert "paper" not in b._base
    assert "api.alpaca.markets" in b._base
