from __future__ import annotations

import logging
from typing import Iterable, List

from ..config import Config, env

log = logging.getLogger(__name__)


def _client():
    try:
        import praw
    except ImportError:
        log.warning("praw not installed; Reddit sentiment disabled.")
        return None
    client_id = env("REDDIT_CLIENT_ID")
    client_secret = env("REDDIT_CLIENT_SECRET")
    user_agent = env("REDDIT_USER_AGENT", "stock-bot/0.1")
    if not (client_id and client_secret):
        log.info("Reddit credentials not set; skipping Reddit scan.")
        return None
    try:
        return praw.Reddit(
            client_id=client_id,
            client_secret=client_secret,
            user_agent=user_agent,
            check_for_async=False,
        )
    except Exception as exc:
        log.warning("Could not init Reddit client: %s", exc)
        return None


def fetch_mentions(cfg: Config, tickers: Iterable[str]) -> dict[str, List[str]]:
    """Return {ticker: [text, ...]} of recent posts/comments mentioning each ticker."""
    tickers = {t.upper() for t in tickers}
    out: dict[str, List[str]] = {t: [] for t in tickers}
    reddit = _client()
    if reddit is None:
        return out
    subs = cfg.sentiment.get("reddit_subs", [])
    post_limit = int(cfg.sentiment.get("reddit_post_limit", 100))
    comment_limit = int(cfg.sentiment.get("reddit_comment_limit", 25))
    for sub_name in subs:
        try:
            sub = reddit.subreddit(sub_name)
            for post in sub.hot(limit=post_limit):
                blob = f"{post.title}\n{post.selftext or ''}".upper()
                for t in tickers:
                    if t in blob or f"${t}" in blob:
                        out[t].append(f"{post.title} {post.selftext or ''}")
                        try:
                            post.comments.replace_more(limit=0)
                            for c in post.comments[:comment_limit]:
                                body = getattr(c, "body", "") or ""
                                if t in body.upper() or f"${t}" in body.upper():
                                    out[t].append(body)
                        except Exception:
                            continue
        except Exception as exc:
            log.warning("Reddit fetch failed for r/%s: %s", sub_name, exc)
            continue
    return out
