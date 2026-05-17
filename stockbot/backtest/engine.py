from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable, Dict, List, Optional

import pandas as pd

from .costs import CostModel, default_equity_costs
from .metrics import Metrics, compute_metrics

log = logging.getLogger(__name__)


@dataclass
class Trade:
    ticker: str
    entry_date: str
    entry_price: float
    exit_date: Optional[str]
    exit_price: Optional[float]
    quantity: float
    direction: str
    pnl: float
    fees: float

    @property
    def closed(self) -> bool:
        return self.exit_date is not None


@dataclass
class BacktestResult:
    equity_curve: pd.Series
    trades: List[Trade]
    metrics: Metrics
    config: dict = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    def attribution_by_ticker(self) -> Dict[str, float]:
        out: Dict[str, float] = {}
        for t in self.trades:
            out[t.ticker] = out.get(t.ticker, 0.0) + t.pnl
        return out


# A SignalFn takes a date and a price-history dict (history sliced up to that date)
# and returns desired *target weights* per ticker. This enforces point-in-time
# discipline: the signal only sees data available as of `asof`.
SignalFn = Callable[[date, Dict[str, pd.DataFrame]], Dict[str, float]]


class Backtester:
    """Event-driven daily backtester with point-in-time data slicing and costs.

    Usage:
        bt = Backtester(price_history, starting_cash=100_000)
        result = bt.run(signal_fn, start='2020-01-01', end='2023-12-31')

    `price_history` should be {ticker: DataFrame} with DatetimeIndex and columns
    including 'Close' and optionally 'Volume' (for slippage).
    """

    def __init__(
        self,
        price_history: Dict[str, pd.DataFrame],
        starting_cash: float = 100_000.0,
        cost_model: Optional[CostModel] = None,
        max_position_weight: float = 0.10,
        rebalance_freq: int = 5,
    ):
        self.history = {t.upper(): self._normalize_index(df) for t, df in price_history.items()}
        self.starting_cash = starting_cash
        self.costs = cost_model or default_equity_costs()
        self.max_position_weight = max_position_weight
        self.rebalance_freq = max(1, rebalance_freq)

    @staticmethod
    def _normalize_index(df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty:
            return df
        d = df.copy()
        if not isinstance(d.index, pd.DatetimeIndex):
            d.index = pd.to_datetime(d.index)
        return d.sort_index()

    def _trading_dates(self, start: str, end: str) -> list[pd.Timestamp]:
        dates = set()
        for df in self.history.values():
            if df is None or df.empty:
                continue
            window = df.loc[start:end]
            dates.update(window.index)
        return sorted(dates)

    def _slice_as_of(self, asof: pd.Timestamp) -> Dict[str, pd.DataFrame]:
        """Return history strictly up to and including `asof` — point-in-time."""
        return {t: df.loc[:asof] for t, df in self.history.items() if df is not None and not df.empty}

    def run(
        self,
        signal_fn: SignalFn,
        start: str,
        end: str,
        verbose: bool = False,
    ) -> BacktestResult:
        dates = self._trading_dates(start, end)
        if not dates:
            return BacktestResult(pd.Series(dtype=float), [], compute_metrics(pd.Series(dtype=float)))

        cash = self.starting_cash
        # positions: ticker -> {quantity, entry_price, entry_date}
        positions: Dict[str, dict] = {}
        trades: List[Trade] = []
        equity_series: list[tuple[pd.Timestamp, float]] = []
        last_rebalance = -1

        for i, day in enumerate(dates):
            sliced = self._slice_as_of(day)
            # Mark-to-market on today's close.
            mtm = 0.0
            for t, pos in positions.items():
                px = self._price_on(t, day)
                if px is None:
                    continue
                sign = 1.0 if pos["direction"] == "long" else -1.0
                mtm += sign * pos["quantity"] * px
            equity = cash + mtm

            if i - last_rebalance >= self.rebalance_freq or i == 0:
                last_rebalance = i
                try:
                    target_weights = signal_fn(day.date(), sliced)
                except Exception as exc:
                    log.warning("signal_fn raised on %s: %s", day, exc)
                    target_weights = {}
                cash, positions, day_trades = self._rebalance(
                    day, target_weights, positions, cash, equity
                )
                trades.extend(day_trades)

            equity_series.append((day, equity))
            if verbose and i % 20 == 0:
                log.info("BT %s equity %.0f cash %.0f pos %d", day.date(), equity, cash, len(positions))

        # Close any remaining positions at final close.
        final_day = dates[-1]
        for t, pos in list(positions.items()):
            px = self._price_on(t, final_day) or pos["entry_price"]
            cash_delta, trade = self._close(t, pos, px, final_day)
            cash += cash_delta
            trades.append(trade)
        positions.clear()
        equity_series.append((final_day, cash))

        eq = pd.Series({d: v for d, v in equity_series})
        metrics = compute_metrics(eq, [t.__dict__ for t in trades if t.closed])
        return BacktestResult(equity_curve=eq, trades=trades, metrics=metrics)

    def _price_on(self, ticker: str, day: pd.Timestamp) -> Optional[float]:
        df = self.history.get(ticker.upper())
        if df is None or df.empty:
            return None
        try:
            return float(df.loc[df.index <= day, "Close"].iloc[-1])
        except (IndexError, KeyError):
            return None

    def _rebalance(
        self,
        day: pd.Timestamp,
        target_weights: Dict[str, float],
        positions: Dict[str, dict],
        cash: float,
        equity: float,
    ) -> tuple[float, Dict[str, dict], List[Trade]]:
        trades: List[Trade] = []
        # Apply position cap.
        capped = {
            t.upper(): max(-self.max_position_weight, min(self.max_position_weight, w))
            for t, w in target_weights.items()
        }

        # Close anything not in target.
        for t in list(positions.keys()):
            if t not in capped or capped[t] == 0:
                px = self._price_on(t, day)
                if px is None:
                    continue
                cash_delta, trade = self._close(t, positions.pop(t), px, day)
                cash += cash_delta
                trades.append(trade)

        # Open / resize the rest.
        for t, weight in capped.items():
            if weight == 0:
                continue
            px = self._price_on(t, day)
            if px is None or px <= 0:
                continue
            target_dollars = equity * weight
            target_qty = target_dollars / px
            current = positions.get(t)
            current_qty = current["quantity"] * (1 if current["direction"] == "long" else -1) if current else 0.0
            delta = target_qty - current_qty
            if abs(delta) < 1e-6:
                continue
            side = "buy" if delta > 0 else "sell"
            fee = self.costs.equity_cost(abs(delta), px, side=side)
            cash -= delta * px + fee
            if current:
                # Realize partial PnL on the opposite side if we cross zero.
                if (delta > 0) == (current["direction"] == "short"):
                    qty_closing = min(abs(delta), current["quantity"])
                    sign = 1.0 if current["direction"] == "long" else -1.0
                    pnl = sign * (px - current["entry_price"]) * qty_closing - fee
                    trades.append(Trade(
                        ticker=t,
                        entry_date=current["entry_date"],
                        entry_price=current["entry_price"],
                        exit_date=str(day.date()),
                        exit_price=px,
                        quantity=qty_closing,
                        direction=current["direction"],
                        pnl=pnl,
                        fees=fee,
                    ))
                    new_qty = current["quantity"] - qty_closing
                    if new_qty <= 0:
                        positions.pop(t, None)
                    else:
                        positions[t] = {**current, "quantity": new_qty}
                else:
                    positions[t]["quantity"] = current["quantity"] + abs(delta)
            else:
                positions[t] = {
                    "quantity": abs(target_qty),
                    "direction": "long" if weight > 0 else "short",
                    "entry_price": px,
                    "entry_date": str(day.date()),
                }
        return cash, positions, trades

    def _close(self, ticker: str, pos: dict, exit_px: float, day: pd.Timestamp) -> tuple[float, Trade]:
        """Close `pos` at `exit_px`. Returns (cash_delta, trade). Callers must add the delta to cash."""
        fee = self.costs.equity_cost(pos["quantity"], exit_px, side="sell")
        sign = 1.0 if pos["direction"] == "long" else -1.0
        proceeds = sign * pos["quantity"] * exit_px
        pnl = sign * (exit_px - pos["entry_price"]) * pos["quantity"] - fee
        cash_delta = proceeds - fee
        return cash_delta, Trade(
            ticker=ticker,
            entry_date=pos["entry_date"],
            entry_price=pos["entry_price"],
            exit_date=str(day.date()),
            exit_price=exit_px,
            quantity=pos["quantity"],
            direction=pos["direction"],
            pnl=pnl,
            fees=fee,
        )
