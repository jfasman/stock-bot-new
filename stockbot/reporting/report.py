from __future__ import annotations

from typing import Iterable

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ..config import Config
from ..data import options as opt_data
from ..data import prices as price_data
from ..portfolio.portfolio import Portfolio, Position
from ..portfolio.targets import evaluate
from ..strategy.ideas import Idea

console = Console()


def _mark(pos: Position) -> float | None:
    if pos.instrument == "equity":
        return price_data.get_last_price(pos.ticker)
    try:
        calls, puts = opt_data.get_chain(pos.ticker, pos.option_expiration or "")
        pool = calls if pos.instrument == "call" else puts
        for c in pool:
            if c.symbol == pos.option_symbol:
                return c.mid
    except Exception:
        return None
    return None


def render_ideas(ideas: Iterable[Idea]) -> None:
    table = Table(title="Ranked ideas", show_lines=False)
    table.add_column("Ticker")
    table.add_column("Side")
    table.add_column("Score", justify="right")
    table.add_column("Last", justify="right")
    table.add_column("RSI", justify="right")
    table.add_column("Sentiment", justify="right")
    table.add_column("Details")
    for i in ideas:
        side = i.instrument.upper()
        if i.instrument != "equity":
            strike = f"{i.option_strike:.1f}" if i.option_strike else "?"
            exp = i.option_expiration or "?"
            side = f"{i.instrument.upper()} {strike} {exp}"
        table.add_row(
            i.ticker,
            side,
            f"{i.score:+.2f}",
            f"{i.last_price:.2f}",
            f"{i.rsi:.1f}",
            f"{i.sentiment_net:+.2f}/{i.sentiment_confidence:.2f}",
            "; ".join(i.reasons[:4]),
        )
    console.print(table)


def render_portfolio(cfg: Config, portfolio: Portfolio) -> None:
    open_positions = portfolio.list_open()
    marks: dict[str, float] = {}
    unrealized = 0.0

    table = Table(title="Open positions", show_lines=False)
    table.add_column("ID")
    table.add_column("Ticker")
    table.add_column("Type")
    table.add_column("Dir")
    table.add_column("Qty", justify="right")
    table.add_column("Entry", justify="right")
    table.add_column("Mark", justify="right")
    table.add_column("P&L", justify="right")
    table.add_column("Stop / Target")

    for p in open_positions:
        mark = _mark(p) or p.entry_price
        key = p.option_symbol or p.ticker
        marks[key] = mark
        pnl = p.unrealized_pnl(mark)
        unrealized += pnl
        type_label = p.instrument.upper()
        if p.instrument != "equity":
            strike = f"{p.option_strike:.1f}" if p.option_strike else "?"
            exp = p.option_expiration or "?"
            type_label = f"{p.instrument.upper()} {strike} {exp}"
        st = f"{p.stop_price:.2f}" if p.stop_price else "—"
        tg = f"{p.target_price:.2f}" if p.target_price else "—"
        table.add_row(
            str(p.id), p.ticker, type_label, p.direction, f"{p.quantity:.0f}",
            f"{p.entry_price:.2f}", f"{mark:.2f}", f"{pnl:+,.2f}", f"{st} / {tg}",
        )

    equity_value = portfolio.equity(marks)
    target_status = evaluate(cfg, portfolio.starting_cash, portfolio.started_at, equity_value)

    summary = (
        f"[bold]Cash:[/bold] ${portfolio.cash:,.2f}    "
        f"[bold]Equity:[/bold] ${equity_value:,.2f}    "
        f"[bold]Unrealized:[/bold] ${unrealized:+,.2f}\n"
        f"{target_status.headline()}"
    )
    console.print(Panel(summary, title="Portfolio"))
    if open_positions:
        console.print(table)
    else:
        console.print("[dim]No open positions.[/dim]")


def render_closed(portfolio: Portfolio, limit: int = 20) -> None:
    closed = portfolio.list_closed()[:limit]
    if not closed:
        console.print("[dim]No closed trades yet.[/dim]")
        return
    table = Table(title=f"Recent closed trades (last {len(closed)})")
    table.add_column("ID")
    table.add_column("Ticker")
    table.add_column("Type")
    table.add_column("Dir")
    table.add_column("Entry", justify="right")
    table.add_column("Exit", justify="right")
    table.add_column("P&L", justify="right")
    table.add_column("Notes")
    total = 0.0
    for p in closed:
        total += p.realized_pnl or 0.0
        table.add_row(
            str(p.id), p.ticker, p.instrument, p.direction,
            f"{p.entry_price:.2f}", f"{p.exit_price:.2f}" if p.exit_price else "—",
            f"{(p.realized_pnl or 0):+,.2f}", (p.notes or "")[:60],
        )
    console.print(table)
    console.print(f"[bold]Realized P&L (last {len(closed)}):[/bold] ${total:+,.2f}")
