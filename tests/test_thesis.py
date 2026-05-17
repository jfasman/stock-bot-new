from dataclasses import dataclass

from stockbot.strategy.thesis import SignalReading, Thesis, thesis_from_idea


@dataclass
class _MockIdea:
    ticker: str = "AAPL"
    direction: str = "long"
    instrument: str = "equity"
    score: float = 0.7
    last_price: float = 200.0
    rsi: float = 55.0
    reasons: list = None
    sentiment_net: float = 0.2
    sentiment_confidence: float = 0.8
    option_symbol: str = None
    option_strike: float = None
    option_expiration: str = None
    option_mid: float = None


def test_thesis_from_idea_preserves_core_fields():
    idea = _MockIdea(reasons=["RSI 55", "above 50d SMA"])
    t = thesis_from_idea(idea, suggested_weight=0.08, sizing_rationale="kelly q")
    assert t.ticker == "AAPL"
    assert t.suggested_weight == 0.08
    assert t.score == 0.7
    assert any(s.name == "sentiment_net" for s in t.signals)


def test_thesis_headline():
    t = Thesis(ticker="MSFT", instrument="equity", direction="long", score=0.55)
    assert "MSFT" in t.headline()
    assert "LONG" in t.headline()


def test_expected_band():
    t = Thesis(ticker="X", instrument="equity", direction="long", score=0.5,
               expected_return=0.10, return_stdev=0.05)
    band = t.expected_band()
    assert band is not None
    lo, hi = band
    assert abs(lo - 0.05) < 1e-9
    assert abs(hi - 0.15) < 1e-9
