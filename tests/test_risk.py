from stockbot.config import Config
from stockbot.portfolio.risk import size_equity, size_option


def _cfg():
    return Config(raw={
        "portfolio": {"max_position_pct": 0.10},
        "risk": {
            "stop_loss_pct": 0.07,
            "take_profit_pct": 0.15,
            "option_stop_loss_pct": 0.40,
            "option_take_profit_pct": 0.75,
        },
        "options": {"max_premium_pct": 0.03},
    })


def test_size_equity_long():
    s = size_equity(_cfg(), equity=100_000, last_price=200.0, direction="long")
    assert s.quantity == 50.0  # 10% of 100k = 10k / $200 = 50
    assert s.stop_price == round(200 * 0.93, 2)
    assert s.target_price == round(200 * 1.15, 2)


def test_size_equity_short_inverts_stop():
    s = size_equity(_cfg(), equity=100_000, last_price=100.0, direction="short")
    assert s.stop_price > 100.0
    assert s.target_price < 100.0


def test_size_option_caps_premium():
    s = size_option(_cfg(), equity=100_000, contract_mid=5.0)
    # 3% of 100k = 3000; 5*100 = 500 per contract -> 6 contracts
    assert s.quantity == 6.0
    assert s.cost == 3000.0
