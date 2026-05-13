from __future__ import annotations

from typing import List

from ..config import Config


def resolve_universe(cfg: Config, override: List[str] | None = None) -> List[str]:
    """Return the list of tickers to scan.

    `override` (e.g. from --watchlist CLI flag) wins; otherwise config watchlist.
    """
    if override:
        return [t.strip().upper() for t in override if t.strip()]
    return cfg.watchlist
