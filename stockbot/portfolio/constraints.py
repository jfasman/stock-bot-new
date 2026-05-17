from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class ConstraintConfig:
    max_position_pct: float = 0.08
    max_sector_pct: float = 0.30
    max_gross_exposure: float = 1.50      # 150% gross (long + short)
    max_net_exposure: float = 1.00        # 100% net (long - short)
    target_beta_range: tuple[float, float] = (-0.20, 1.20)


@dataclass
class ProposedPosition:
    ticker: str
    sector: Optional[str]
    weight: float          # signed: positive long, negative short
    beta: Optional[float] = None


@dataclass
class ConstraintCheck:
    ok: bool
    breaches: List[str] = field(default_factory=list)


def check_constraints(
    proposed: List[ProposedPosition],
    cfg: ConstraintConfig,
) -> ConstraintCheck:
    """Validate a proposed portfolio against position, sector, exposure, and beta limits."""
    breaches: List[str] = []
    by_sector: Dict[str, float] = {}
    gross = 0.0
    net = 0.0
    weighted_beta_num = 0.0
    weighted_beta_den = 0.0
    for p in proposed:
        if abs(p.weight) > cfg.max_position_pct + 1e-9:
            breaches.append(f"{p.ticker}: weight {p.weight:.2%} > max {cfg.max_position_pct:.2%}")
        sec = p.sector or "_UNKNOWN_"
        by_sector[sec] = by_sector.get(sec, 0.0) + p.weight
        gross += abs(p.weight)
        net += p.weight
        if p.beta is not None:
            weighted_beta_num += p.beta * abs(p.weight)
            weighted_beta_den += abs(p.weight)

    for sec, w in by_sector.items():
        if abs(w) > cfg.max_sector_pct + 1e-9:
            breaches.append(f"sector {sec}: {w:.2%} > max {cfg.max_sector_pct:.2%}")

    if gross > cfg.max_gross_exposure + 1e-9:
        breaches.append(f"gross {gross:.2%} > max {cfg.max_gross_exposure:.2%}")
    if abs(net) > cfg.max_net_exposure + 1e-9:
        breaches.append(f"|net| {abs(net):.2%} > max {cfg.max_net_exposure:.2%}")

    if weighted_beta_den > 0:
        port_beta = weighted_beta_num / weighted_beta_den
        lo, hi = cfg.target_beta_range
        if not (lo <= port_beta <= hi):
            breaches.append(f"portfolio beta {port_beta:.2f} outside [{lo:.2f}, {hi:.2f}]")

    return ConstraintCheck(ok=not breaches, breaches=breaches)


def correlation_warning(corr_matrix, threshold: float = 0.85) -> List[str]:
    """Return human-readable warnings for pairs above the correlation threshold."""
    msgs: List[str] = []
    if corr_matrix is None or corr_matrix.empty:
        return msgs
    tickers = list(corr_matrix.columns)
    for i, a in enumerate(tickers):
        for b in tickers[i + 1:]:
            try:
                rho = float(corr_matrix.loc[a, b])
            except Exception:
                continue
            if abs(rho) >= threshold:
                msgs.append(f"high correlation {a}-{b}: {rho:.2f}")
    return msgs
