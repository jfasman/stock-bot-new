from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

# Lightweight lexicon-based scorer. Intentionally simple — keeps the project
# free of heavy NLP deps while still capturing the dominant signal on retail
# forums where vocabulary is highly stylized.

_BULL_TERMS = {
    "moon", "rocket", "buy", "long", "calls", "call", "bullish", "rip", "ripping",
    "breakout", "squeeze", "gamma", "yolo", "tendies", "🚀", "🌙", "💎", "🤝",
    "diamond hands", "to the moon", "all in", "loaded up", "lfg", "send it",
    "ath", "all time high", "beat", "beats", "beat earnings", "upgrade", "upgraded",
    "outperform", "strong buy", "accumulate",
}
_BEAR_TERMS = {
    "puts", "put", "short", "shorting", "bearish", "drilling", "dump", "dumping",
    "crash", "rug", "rugpull", "bagholder", "guh", "bear", "down bad", "tanking",
    "miss", "missed", "missed earnings", "downgrade", "downgraded", "underperform",
    "sell", "selling", "exit", "stop loss",
}

_TICKER_RE = re.compile(r"(?:^|[\s,])\$?([A-Z]{1,5})(?=[\s,.!?:;)\]]|$)")


@dataclass
class SentimentScore:
    samples: int
    bull_hits: int
    bear_hits: int

    @property
    def net(self) -> float:
        """Bounded score in [-1, 1]. 0 when no signal."""
        total = self.bull_hits + self.bear_hits
        if total == 0:
            return 0.0
        return (self.bull_hits - self.bear_hits) / total

    @property
    def confidence(self) -> float:
        """Crude confidence based on volume of mentions, capped at 1.0."""
        return min(1.0, (self.bull_hits + self.bear_hits) / 25.0)


def score_text(text: str) -> tuple[int, int]:
    """Return (bull_hits, bear_hits) for a single text blob."""
    if not text:
        return 0, 0
    lowered = text.lower()
    bull = sum(1 for term in _BULL_TERMS if term in lowered)
    bear = sum(1 for term in _BEAR_TERMS if term in lowered)
    return bull, bear


def score_corpus(texts: Iterable[str]) -> SentimentScore:
    bull_total = bear_total = samples = 0
    for t in texts:
        b, s = score_text(t)
        bull_total += b
        bear_total += s
        samples += 1
    return SentimentScore(samples=samples, bull_hits=bull_total, bear_hits=bear_total)


def extract_tickers(text: str, universe: set[str] | None = None) -> set[str]:
    """Pull cashtags and uppercase tokens that look like tickers.

    If a `universe` is provided, results are filtered to only known symbols —
    this filters out the high false-positive rate of bare uppercase words.
    """
    found = {m.group(1) for m in _TICKER_RE.finditer(text or "")}
    if universe is not None:
        return found & universe
    return found
