from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CostModel:
    """Realistic transaction-cost model. Defaults are reasonable for retail."""

    commission_per_share: float = 0.0          # $/share — most US retail brokers are zero
    commission_per_contract: float = 0.65      # $/contract for options
    min_commission: float = 0.0
    half_spread_bps: float = 5.0               # 5 bps each side on equities
    option_spread_pct: float = 0.05            # 5% of mid for options spread
    impact_coefficient: float = 0.1            # √-law impact: bps = coef * sqrt(order / adv) * 1e4
    fee_finra_per_share: float = 0.000166      # SEC/FINRA fees on sells (small, conservative)

    def equity_cost(self, shares: float, price: float, adv: float | None = None, side: str = "buy") -> float:
        """Return total expected cost in dollars for an equity order."""
        if shares <= 0 or price <= 0:
            return 0.0
        notional = shares * price
        commission = max(self.min_commission, shares * self.commission_per_share)
        spread = notional * (self.half_spread_bps / 10_000.0)
        impact = 0.0
        if adv and adv > 0:
            participation = shares / adv
            if participation > 0:
                impact_bps = self.impact_coefficient * (participation ** 0.5) * 10_000.0
                impact = notional * impact_bps / 10_000.0
        fees = shares * self.fee_finra_per_share if side == "sell" else 0.0
        return commission + spread + impact + fees

    def option_cost(self, contracts: float, mid: float, side: str = "buy") -> float:
        """For options: the bid-ask IS the cost. Don't assume mid fills."""
        if contracts <= 0 or mid <= 0:
            return 0.0
        commission = max(self.min_commission, contracts * self.commission_per_contract)
        half_spread = mid * self.option_spread_pct / 2.0
        spread_cost = contracts * 100 * half_spread
        return commission + spread_cost


def default_equity_costs() -> CostModel:
    return CostModel()


def default_option_costs() -> CostModel:
    return CostModel(half_spread_bps=0, option_spread_pct=0.10)
