from stockbot.engine.orders import Quote, build_equity_order, limit_from_book


def test_limit_from_book_midpoint():
    q = Quote(bid=99.0, ask=101.0)
    assert abs(limit_from_book(q, "buy", aggression=0.5) - 100.0) < 1e-6
    assert abs(limit_from_book(q, "sell", aggression=0.5) - 100.0) < 1e-6


def test_limit_passive_post_at_book():
    q = Quote(bid=50.0, ask=50.5)
    assert limit_from_book(q, "buy", aggression=0.0) == 50.0
    assert limit_from_book(q, "sell", aggression=0.0) == 50.5


def test_limit_aggressive_crosses_spread():
    q = Quote(bid=10.0, ask=10.10)
    assert limit_from_book(q, "buy", aggression=1.0) == 10.10
    assert limit_from_book(q, "sell", aggression=1.0) == 10.00


def test_build_equity_order_returns_limit():
    q = Quote(bid=50, ask=50.20)
    o = build_equity_order("AAPL", "buy", 100, q)
    assert o.ticker == "AAPL"
    assert o.side == "buy"
    assert o.quantity == 100
    assert o.limit_price is not None and o.limit_price > 0
