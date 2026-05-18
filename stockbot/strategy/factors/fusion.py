"""Live-side wiring for factor fusion (roadmap §13.6).

This is the seam between the per-ticker scoring loop (in `strategy/ideas.py` and
`strategy/score_explain.py`) and the cross-sectional factor model (in
`strategy/factors/composite.py`).

The function is pure: callers inject `price_loader` and `fundamentals_loader`
so the strategy module stays IO-free. The engine and `score_explain` provide
real loaders; tests inject stubs.

Returns `{}` when factor fusion should be skipped — either disabled in config
(`factor_weight == 0`), or the assembled universe is below `factors.min_universe`,
or fetching peer data raised. Callers treat an empty dict as "no factor leg" and
fall back to the two-way blend in `composite_score`.
"""
from __future__ import annotations

import logging
from typing import Callable, Dict, Iterable, Optional

import pandas as pd

from ...config import Config
from ...data.fundamentals import Fundamentals
from .composite import FactorWeights, score_universe
from .universe import build_universe, resolve_peer_pool

log = logging.getLogger(__name__)


def compute_factor_composites(
    cfg: Config,
    targets: Iterable[str],
    *,
    fundamentals_loader: Callable[[str], Fundamentals],
    price_loader: Callable[[str], pd.DataFrame],
    weights: Optional[FactorWeights] = None,
) -> Dict[str, float]:
    """Score `targets` cross-sectionally against the configured peer pool.

    Returns a mapping ticker → factor composite (in roughly [-1, 1]). Tickers
    in `targets` whose composite couldn't be computed are simply absent —
    callers should treat absence as "no factor leg for this ticker."
    """
    targets = [t.upper() for t in targets]
    factor_cfg = cfg.raw.get("factors", {}) if hasattr(cfg, "raw") else {}
    factor_weight = float(cfg.strategy.get("factor_weight", 0.0))
    if factor_weight <= 0:
        return {}

    peer_pool = resolve_peer_pool(list(factor_cfg.get("peer_pool", [])))
    min_universe = int(factor_cfg.get("min_universe", 12))
    sector_neutral = bool(factor_cfg.get("sector_neutral", True))
    watchlist = list(cfg.watchlist) if hasattr(cfg, "watchlist") else []

    # Fundamentals load is required for sector matching and the factor math
    # itself. Wrap so a flaky vendor doesn't blow up the live cycle.
    candidate_tickers = sorted({*targets, *(s.upper() for s in watchlist), *peer_pool})
    fundamentals_map: Dict[str, Fundamentals] = {}
    for t in candidate_tickers:
        try:
            fundamentals_map[t] = fundamentals_loader(t)
        except Exception as exc:
            log.info("Fundamentals unavailable for %s: %s", t, exc)

    universe = build_universe(
        targets,
        watchlist=watchlist,
        peer_pool=peer_pool,
        fundamentals=fundamentals_map,
        min_universe=min_universe,
    )
    if universe is None:
        log.info(
            "Factor universe below min_universe=%d (have %d candidates) — "
            "live blend falls back to two-way",
            min_universe,
            len(candidate_tickers),
        )
        return {}

    price_history: Dict[str, pd.DataFrame] = {}
    for t in universe:
        try:
            price_history[t] = price_loader(t)
        except Exception as exc:
            log.info("Price history unavailable for %s: %s", t, exc)
            price_history[t] = pd.DataFrame()

    try:
        report = score_universe(
            universe,
            fundamentals_map,
            price_history,
            weights=weights,
            sector_neutral=sector_neutral,
        )
    except Exception as exc:
        log.warning("Factor scoring failed: %s", exc)
        return {}

    return {t: report.composite[t] for t in targets if t in report.composite}
