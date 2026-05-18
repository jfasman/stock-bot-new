"""Assemble the cross-sectional ranking universe for a factor scan.

Factors are cross-sectional by definition — they need peers to be meaningful.
This module is the seam between "what ticker(s) is the engine scoring right now"
and "what set of names do we rank them against." The function is pure: it takes
a target and the configured pools and produces a ticker list. The caller is
responsible for fetching fundamentals and prices for the assembled universe.

Roadmap §13.6: returns None when the assembled universe is below
``factors.min_universe`` so the live blend can fall back to the two-way mix
(same pattern sentiment uses when Reddit creds are missing).
"""
from __future__ import annotations

from typing import Iterable, Mapping, Optional, Sequence

from ...data.fundamentals import Fundamentals


def build_universe(
    target: str | Iterable[str],
    *,
    watchlist: Iterable[str] = (),
    peer_pool: Iterable[str] = (),
    fundamentals: Optional[Mapping[str, Fundamentals]] = None,
    min_universe: int = 12,
    sector_match: bool = True,
) -> Optional[list[str]]:
    """Return the ranking universe for `target` — watchlist ∪ sector-matched peers.

    The watchlist is included in full (user's curated set). The peer pool is
    filtered to peers sharing the target's sector when:
      - `sector_match` is True, AND
      - we have a fundamentals row for the target (so we know its sector), AND
      - the target's sector is non-None.
    Otherwise the peer pool is included unfiltered — we'd rather degrade to a
    cross-sector ranking than refuse to score.

    Returns None when the assembled universe (deduplicated) is smaller than
    `min_universe`. The caller treats None as "skip the factor leg."
    """
    fundamentals = fundamentals or {}
    targets = [target] if isinstance(target, str) else list(target)
    targets = [t.upper() for t in targets]

    target_sectors: set[str] = set()
    if sector_match:
        for t in targets:
            f = fundamentals.get(t)
            if f and f.sector:
                target_sectors.add(f.sector)

    peers = [p.upper() for p in peer_pool]
    if sector_match and target_sectors:
        filtered = []
        for p in peers:
            fp = fundamentals.get(p)
            if fp and fp.sector and fp.sector in target_sectors:
                filtered.append(p)
        peers = filtered

    universe: list[str] = []
    seen: set[str] = set()
    for t in (*targets, *(s.upper() for s in watchlist), *peers):
        if t not in seen:
            universe.append(t)
            seen.add(t)

    if len(universe) < min_universe:
        return None
    return universe


def resolve_peer_pool(peer_pool_config: Sequence[str]) -> list[str]:
    """Resolve a peer-pool config entry into a literal ticker list.

    Config may contain literal tickers (``AAPL``) or named tokens
    (``SPY_constituents``). Named tokens are placeholders until a constituents
    file ships; for now they expand to an empty list and the caller falls back
    to whatever literal tickers are present.
    """
    out: list[str] = []
    for entry in peer_pool_config:
        if entry in _NAMED_POOLS:
            out.extend(_NAMED_POOLS[entry])
        else:
            out.append(entry.upper())
    seen: set[str] = set()
    deduped = []
    for t in out:
        if t not in seen:
            deduped.append(t)
            seen.add(t)
    return deduped


_NAMED_POOLS: dict[str, list[str]] = {
    # Placeholder. Populating these maps from a constituents file is a
    # follow-up — for now configured literal tickers carry the load.
    "SPY_constituents": [],
}
