from stockbot.sentiment.nlp import extract_tickers, score_corpus, score_text


def test_score_text_bullish():
    bull, bear = score_text("$NVDA to the moon, loaded up calls 🚀")
    assert bull > bear


def test_score_text_bearish():
    bull, bear = score_text("Buying puts on TSLA, this thing is drilling, total bagholder territory")
    assert bear > bull


def test_score_corpus_aggregates():
    score = score_corpus([
        "huge breakout, calls printing",
        "this is dumping, puts only",
        "neutral take, just watching",
    ])
    assert score.samples == 3
    assert -1.0 <= score.net <= 1.0


def test_extract_tickers_with_universe():
    text = "I like $AAPL and MSFT but not TOTALLYFAKE"
    found = extract_tickers(text, universe={"AAPL", "MSFT", "NVDA"})
    assert found == {"AAPL", "MSFT"}
