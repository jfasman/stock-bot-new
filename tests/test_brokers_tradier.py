"""Tests for engine/brokers/tradier.py — read-only Tradier client.

Tradier's single-row-as-object quirk is the interesting bit: when a query
returns one position, the JSON looks like ``{"position": {...}}``; with two,
``{"position": [{...}, {...}]}``. The helper must handle both.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest

from stockbot.engine.brokers.tradier import TradierLiveBroker


def _mock_response(json_data, status: int = 200):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = json_data
    r.text = str(json_data)
    return r


def _broker(session) -> TradierLiveBroker:
    return TradierLiveBroker(
        access_token="TOKEN", account_id="VA000001",
        sandbox=True, http_session=session,
    )


def test_account_summary_maps_top_level_fields():
    session = MagicMock()
    session.get.return_value = _mock_response({
        "balances": {
            "account_number": "VA000001",
            "total_cash": "1500.00",
            "total_equity": "5000.00",
            "margin": {"stock_buying_power": "10000.00"},
        }
    })
    summary = _broker(session).account_summary()
    assert summary.account_id == "VA000001"
    assert summary.cash == 1500.00
    assert summary.equity == 5000.00
    assert summary.buying_power == 10000.00


def test_account_summary_falls_back_to_cash_available_when_no_margin():
    session = MagicMock()
    session.get.return_value = _mock_response({
        "balances": {
            "total_cash": "2000.00",
            "total_equity": "2000.00",
            "cash": {"cash_available": "1800.00"},
        }
    })
    summary = _broker(session).account_summary()
    assert summary.buying_power == 1800.00


def test_positions_handles_single_row_dict_shape():
    """Tradier returns one position as a dict, not a list. The wrapper must
    normalize so callers always get a list."""
    session = MagicMock()
    session.get.return_value = _mock_response({
        "positions": {"position": {
            "symbol": "AAPL", "quantity": 100, "cost_basis": 15000.00,
        }}
    })
    positions = _broker(session).positions()
    assert len(positions) == 1
    assert positions[0].ticker == "AAPL"
    assert positions[0].quantity == 100
    assert positions[0].avg_entry_price == pytest.approx(150.00)


def test_positions_handles_multi_row_list_shape():
    session = MagicMock()
    session.get.return_value = _mock_response({
        "positions": {"position": [
            {"symbol": "AAPL", "quantity": 100, "cost_basis": 15000.00},
            {"symbol": "MSFT", "quantity": 50, "cost_basis": 15000.00},
        ]}
    })
    positions = _broker(session).positions()
    assert {p.ticker for p in positions} == {"AAPL", "MSFT"}


def test_positions_returns_empty_when_no_positions_key():
    session = MagicMock()
    session.get.return_value = _mock_response({"positions": "null"})  # API quirk
    assert _broker(session).positions() == []


def test_transactions_filters_by_start_date():
    session = MagicMock()
    session.get.return_value = _mock_response({
        "history": {"event": [
            {"date": "2026-05-15", "id": "T1", "trade": {
                "symbol": "AAPL", "quantity": 10, "price": 150.0,
            }},
            {"date": "2026-05-16", "id": "T2", "trade": {
                "symbol": "MSFT", "quantity": -5, "price": 300.0,
            }},
        ]}
    })
    txns = _broker(session).transactions(since=date(2026, 5, 1))
    assert len(txns) == 2
    # quantity sign determines side; absolute value goes into quantity.
    by_id = {t.transaction_id: t for t in txns}
    assert by_id["T1"].side == "buy"
    assert by_id["T2"].side == "sell"
    assert by_id["T2"].quantity == 5


def test_transactions_passes_start_param():
    session = MagicMock()
    session.get.return_value = _mock_response({"history": {"event": []}})
    _broker(session).transactions(since=date(2026, 5, 1))
    _, kwargs = session.get.call_args
    assert kwargs["params"]["start"] == "2026-05-01"
    assert kwargs["params"]["type"] == "trade"


def test_quote_extracts_first_row():
    session = MagicMock()
    session.get.return_value = _mock_response({
        "quotes": {"quote": {
            "symbol": "AAPL", "last": 175.50, "bid": 175.45, "ask": 175.55,
        }}
    })
    q = _broker(session).quote("aapl")
    assert q.ticker == "AAPL"
    assert q.last == 175.50
    assert q.bid == 175.45
    assert q.ask == 175.55


def test_quote_raises_on_empty_result():
    session = MagicMock()
    session.get.return_value = _mock_response({"quotes": "null"})
    with pytest.raises(RuntimeError, match="no quote"):
        _broker(session).quote("BOGUS")


def test_http_error_raises():
    session = MagicMock()
    session.get.return_value = _mock_response({"fault": "..."}, status=403)
    with pytest.raises(RuntimeError, match="403"):
        _broker(session).account_summary()


def test_bearer_token_sent():
    session = MagicMock()
    session.get.return_value = _mock_response({"balances": {}})
    _broker(session).account_summary()
    _, kwargs = session.get.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer TOKEN"


def test_sandbox_url_by_default():
    b = TradierLiveBroker(access_token="T", account_id="A", http_session=MagicMock())
    assert "sandbox" in b._base


def test_production_url_when_sandbox_false():
    b = TradierLiveBroker(access_token="T", account_id="A", sandbox=False, http_session=MagicMock())
    assert "sandbox" not in b._base


def test_no_order_methods_exposed():
    b = _broker(MagicMock())
    assert not hasattr(b, "place_order")
    assert not hasattr(b, "submit_order")
    assert not hasattr(b, "cancel_order")


def test_satisfies_live_broker_protocol():
    from stockbot.engine.brokers.base import LiveBroker
    b = _broker(MagicMock())
    assert isinstance(b, LiveBroker)
