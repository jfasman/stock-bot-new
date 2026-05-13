from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, List

from ..config import Config
from . import reddit as reddit_src
from . import stocktwits as st_src
from .nlp import SentimentScore, score_corpus

log = logging.getLogger(__name__)


@dataclass
class AggregateSentiment:
    ticker: str
    net: float            # -1..1
    confidence: float     # 0..1
    sources: dict         # {source_name: SentimentScore}
    sample_texts: List[str]


def _merge(scores: dict[str, SentimentScore]) -> tuple[float, float]:
    """Weight by source confidence so a noisy source can't dominate."""
    weighted_sum = weight_total = 0.0
    for s in scores.values():
        if s.samples == 0:
            continue
        w = max(0.1, s.confidence)
        weighted_sum += s.net * w
        weight_total += w
    if weight_total == 0:
        return 0.0, 0.0
    net = weighted_sum / weight_total
    confidence = min(1.0, weight_total / 2.0)
    return net, confidence


def aggregate(cfg: Config, tickers: Iterable[str]) -> dict[str, AggregateSentiment]:
    tickers = [t.upper() for t in tickers]
    results: dict[str, AggregateSentiment] = {}

    reddit_hits = reddit_src.fetch_mentions(cfg, tickers)

    for t in tickers:
        per_source: dict[str, SentimentScore] = {}
        sample_texts: List[str] = []

        reddit_texts = reddit_hits.get(t, [])
        if reddit_texts:
            per_source["reddit"] = score_corpus(reddit_texts)
            sample_texts.extend(reddit_texts[:3])

        st_texts, st_bulls, st_bears = st_src.fetch_texts(cfg, t)
        if st_texts:
            # Combine lexicon score with the explicit Bullish/Bearish tags.
            lex = score_corpus(st_texts)
            combined = SentimentScore(
                samples=lex.samples,
                bull_hits=lex.bull_hits + st_bulls * 2,
                bear_hits=lex.bear_hits + st_bears * 2,
            )
            per_source["stocktwits"] = combined
            sample_texts.extend(st_texts[:3])

        net, confidence = _merge(per_source)
        results[t] = AggregateSentiment(
            ticker=t,
            net=net,
            confidence=confidence,
            sources=per_source,
            sample_texts=sample_texts,
        )
    return results
