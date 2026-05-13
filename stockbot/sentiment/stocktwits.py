from __future__ import annotations

import logging
from typing import List

import requests

from ..config import Config

log = logging.getLogger(__name__)

# Public StockTwits streams endpoint — no key required, but rate-limited.
_URL = "https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json"


def fetch_messages(cfg: Config, ticker: str) -> List[dict]:
    limit = int(cfg.sentiment.get("stocktwits_message_limit", 30))
    try:
        resp = requests.get(_URL.format(ticker=ticker), timeout=8)
        if resp.status_code != 200:
            log.debug("StockTwits %s -> HTTP %s", ticker, resp.status_code)
            return []
        data = resp.json() or {}
        messages = data.get("messages", []) or []
        return messages[:limit]
    except Exception as exc:
        log.debug("StockTwits fetch failed for %s: %s", ticker, exc)
        return []


def fetch_texts(cfg: Config, ticker: str) -> tuple[List[str], int, int]:
    """Return (texts, bull_tag_count, bear_tag_count).

    StockTwits messages can carry an explicit sentiment tag we trust as ground truth.
    """
    bulls = bears = 0
    texts: List[str] = []
    for m in fetch_messages(cfg, ticker):
        body = m.get("body", "") or ""
        texts.append(body)
        sent = ((m.get("entities") or {}).get("sentiment") or {}).get("basic")
        if sent == "Bullish":
            bulls += 1
        elif sent == "Bearish":
            bears += 1
    return texts, bulls, bears
