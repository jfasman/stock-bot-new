from stockbot.portfolio.portfolio import Portfolio


def test_open_and_close_equity_position():
    p = Portfolio(starting_cash=10_000)
    pid = p.open_position(
        ticker="AAPL", instrument="equity", direction="long",
        quantity=10, entry_price=100.0, stop_price=93.0, target_price=115.0,
    )
    assert p.cash == 9_000.0
    realized = p.close_position(pid, exit_price=110.0)
    assert realized == 100.0
    assert p.cash == 10_100.0
    assert len(p.list_open()) == 0
    assert len(p.list_closed()) == 1


def test_open_position_rejects_overspend():
    p = Portfolio(starting_cash=1_000)
    try:
        p.open_position("MSFT", "equity", "long", 100, 50.0)
    except ValueError:
        return
    raise AssertionError("expected ValueError for insufficient cash")


def test_option_multiplier_applies():
    p = Portfolio(starting_cash=10_000)
    # 1 call @ $2 mid = $200 cost (100x multiplier)
    pid = p.open_position(
        ticker="NVDA", instrument="call", direction="long",
        quantity=1, entry_price=2.0, option_symbol="NVDA240621C00500000",
        option_strike=500, option_expiration="2024-06-21",
    )
    assert p.cash == 9_800.0
    # close at $3 -> $100 profit
    realized = p.close_position(pid, exit_price=3.0)
    assert realized == 100.0


def test_short_position_pnl_inverted():
    p = Portfolio(starting_cash=10_000)
    pid = p.open_position("TSLA", "equity", "short", 5, 200.0)
    # Short -> price drops to 180 is a win of 5 * 20 = 100
    realized = p.close_position(pid, exit_price=180.0)
    assert realized == 100.0
