from __future__ import annotations

import math
from dataclasses import dataclass

from ..config import Config


@dataclass
class SizingDecision:
    quantity: float            # shares or contracts
    cost: float                # dollars
    stop_price: float | None
    target_price: float | None
    reason: str


def size_equity(
    cfg: Config,
    equity: float,
    last_price: float,
    direction: str,
) -> SizingDecision:
    max_pct = float(cfg.portfolio.get("max_position_pct", 0.08))
    stop_pct = float(cfg.risk.get("stop_loss_pct", 0.07))
    target_pct = float(cfg.risk.get("take_profit_pct", 0.15))
    budget = equity * max_pct
    if last_price <= 0:
        return SizingDecision(0, 0, None, None, "invalid price")
    qty = math.floor(budget / last_price)
    if qty <= 0:
        return SizingDecision(0, 0, None, None, "position smaller than 1 share")
    if direction == "long":
        stop = last_price * (1 - stop_pct)
        target = last_price * (1 + target_pct)
    else:
        stop = last_price * (1 + stop_pct)
        target = last_price * (1 - target_pct)
    return SizingDecision(
        quantity=float(qty),
        cost=qty * last_price,
        stop_price=round(stop, 2),
        target_price=round(target, 2),
        reason=f"equity sizing @ {max_pct*100:.1f}% of {equity:,.0f}",
    )


def size_option(
    cfg: Config,
    equity: float,
    contract_mid: float,
) -> SizingDecision:
    """Position-size an options spend. 1 contract = 100 shares of premium."""
    max_premium_pct = float(cfg.options.get("max_premium_pct", 0.03))
    stop_pct = float(cfg.risk.get("option_stop_loss_pct", 0.40))
    target_pct = float(cfg.risk.get("option_take_profit_pct", 0.75))
    budget = equity * max_premium_pct
    cost_per_contract = contract_mid * 100.0
    if cost_per_contract <= 0:
        return SizingDecision(0, 0, None, None, "invalid premium")
    contracts = math.floor(budget / cost_per_contract)
    if contracts <= 0:
        return SizingDecision(0, 0, None, None, "premium exceeds budget")
    return SizingDecision(
        quantity=float(contracts),
        cost=contracts * cost_per_contract,
        stop_price=round(contract_mid * (1 - stop_pct), 2),
        target_price=round(contract_mid * (1 + target_pct), 2),
        reason=f"options sizing @ {max_premium_pct*100:.1f}% of {equity:,.0f}",
    )
