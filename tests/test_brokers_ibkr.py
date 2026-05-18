"""Tests for engine/brokers/ibkr.py — read-only IBKR Client Portal Gateway client.

IBKR's gateway requires a one-time `/iserver/accounts` call before most
endpoints work; the test confirms that's done lazily on first read.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest

from stockbot.engine.brokers.ibkr import IbkrLiveBroker


def _mock_response(json_data, status: int = 200):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = json_data
    r.text = str(json_data)
    return r


def _broker(session) -> IbkrLiveBroker:
    return IbkrLiveBroker(account_id="U123456", http_session=session)


def test_account_summary_maps_amount_dicts():
    """IBKR returns numeric summary values as `{"amount": N, ...}`."""
    session = MagicMock()
    # First call: /iserver/accounts (lazy select); second: /portfolio/.../summary
    session.get.side_effect = [
        _mock_response({"accounts": ["U123456"]}),
        _mock_response({
            "netliquidation": {"amount": 50000.0},
            "buyingpower": {"amount": 100000.0},
            "totalcashvalue": {"amount": 12000.0},
            "previousdayequitywithloanvalue": {"amount": 49500.0},
        }),
    ]
    summary = _broker(session).account_summary()
    assert summary.account_id == "U123456"
    assert summary.cash == 12000.0
    assert summary.equity == 50000.0
    assert summary.buying_power == 100000.0
    assert summary.last_equity == 49500.0


def test_account_select_called_once_per_session():
    session = MagicMock()
    # First call is the select; subsequent calls are real endpoints.
    session.get.side_effect = [
        _mock_response({"accounts": ["U123456"]}),
        _mock_response({"netliquidation": {"amount": 1.0}, "buyingpower": {"amount": 0.0}, "totalcashvalue": {"amount": 0.0}}),
        _mock_response([]),  # positions
        _mock_response([]),  # trades
    ]
    b = _broker(session)
    b.account_summary()
    b.positions()
    b.transactions(since=date(2026, 1, 1))
    # /iserver/accounts URL should appear exactly once.
    select_calls = [c for c in session.get.call_args_list if "/iserver/accounts" in c[0][0]]
    assert len(select_calls) == 1


def test_positions_maps_rows():
    session = MagicMock()
    session.get.side_effect = [
        _mock_response({"accounts": ["U123456"]}),
        _mock_response([
            {
                "contractDesc": "AAPL", "position": 100, "avgPrice": 150.0,
                "mktPrice": 175.0, "mktValue": 17500.0, "unrealizedPnl": 2500.0,
            },
            {
                "contractDesc": "MSFT NASDAQ", "position": 50, "avgPrice": 300.0,
                "mktPrice": 320.0, "mktValue": 16000.0, "unrealizedPnl": 1000.0,
            },
        ]),
    ]
    positions = _broker(session).positions()
    assert positions[0].ticker == "AAPL"
    assert positions[0].quantity == 100
    # contractDesc may carry suffix info; ticker is the first token.
    assert positions[1].ticker == "MSFT"


def test_transactions_filters_client_side_by_since():
    """The IBKR trades endpoint doesn't accept a date filter; we filter ourselves."""
    session = MagicMock()
    session.get.side_effect = [
        _mock_response({"accounts": ["U123456"]}),
        _mock_response([
            {
                "trade_time": "20260515-14:30:00", "symbol": "AAPL",
                "side": "B", "size": 10, "price": 150.0, "execution_id": "E1",
            },
            {
                "trade_time": "20260301-10:00:00", "symbol": "OLD",
                "side": "B", "size": 5, "price": 100.0, "execution_id": "E0",
            },
        ]),
    ]
    txns = _broker(session).transactions(since=date(2026, 5, 1))
    ids = [t.transaction_id for t in txns]
    assert "E1" in ids
    assert "E0" not in ids  # filtered out — before `since`


def test_quote_uses_secdef_search_then_snapshot():
    session = MagicMock()
    session.get.side_effect = [
        _mock_response({"accounts": ["U123456"]}),       # account select
        _mock_response([{"conid": 265598, "symbol": "AAPL"}]),  # secdef/search
        _mock_response([{"31": 175.50, "84": 175.45, "86": 175.55}]),  # snapshot
    ]
    q = _broker(session).quote("AAPL")
    assert q.ticker == "AAPL"
    assert q.last == 175.50
    assert q.bid == 175.45
    assert q.ask == 175.55


def test_quote_raises_when_no_contract_found():
    session = MagicMock()
    session.get.side_effect = [
        _mock_response({"accounts": ["U123456"]}),
        _mock_response([]),  # empty secdef search
    ]
    with pytest.raises(RuntimeError, match="no contract"):
        _broker(session).quote("BOGUS")


def test_http_error_raises():
    session = MagicMock()
    session.get.return_value = _mock_response({"error": "no auth"}, status=401)
    with pytest.raises(RuntimeError, match="401"):
        _broker(session).account_summary()


def test_no_order_methods_exposed():
    b = _broker(MagicMock())
    assert not hasattr(b, "place_order")
    assert not hasattr(b, "submit_order")
    assert not hasattr(b, "cancel_order")


def test_satisfies_live_broker_protocol():
    from stockbot.engine.brokers.base import LiveBroker
    b = _broker(MagicMock())
    assert isinstance(b, LiveBroker)
