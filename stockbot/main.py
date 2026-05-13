from __future__ import annotations

import logging

import click

from .config import load_config
from .data.universe import resolve_universe
from .engine import paper
from .portfolio.portfolio import Portfolio
from .reporting.report import console, render_closed, render_ideas, render_portfolio
from .strategy.ideas import generate_ideas


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )


@click.group()
@click.option("--verbose", is_flag=True, help="Verbose logging.")
@click.pass_context
def cli(ctx: click.Context, verbose: bool) -> None:
    """stock-bot: paper-trading research bot."""
    _setup_logging(verbose)
    cfg = load_config()
    ctx.ensure_object(dict)
    ctx.obj["cfg"] = cfg
    ctx.obj["portfolio"] = Portfolio(starting_cash=float(cfg.portfolio.get("starting_cash", 100_000)))


@cli.command()
@click.option("--watchlist", "-w", help="Comma-separated tickers; overrides config.")
@click.pass_context
def scan(ctx: click.Context, watchlist: str | None) -> None:
    """Score the watchlist and print ranked ideas (no trades placed)."""
    cfg = ctx.obj["cfg"]
    tickers = resolve_universe(cfg, watchlist.split(",") if watchlist else None)
    console.print(f"[dim]Scanning {len(tickers)} tickers…[/dim]")
    ideas = generate_ideas(cfg, tickers)
    if not ideas:
        console.print("[yellow]No ideas above threshold.[/yellow]")
        return
    render_ideas(ideas)


@cli.group(name="paper")
def paper_cmd() -> None:
    """Paper-trading commands."""


@paper_cmd.command("run")
@click.option("--days", default=1, show_default=True, help="Number of trading cycles to step.")
@click.option("--watchlist", "-w", help="Comma-separated tickers; overrides config.")
@click.pass_context
def paper_run(ctx: click.Context, days: int, watchlist: str | None) -> None:
    """Run the paper-trading loop forward N cycles."""
    cfg = ctx.obj["cfg"]
    portfolio = ctx.obj["portfolio"]
    tickers = resolve_universe(cfg, watchlist.split(",") if watchlist else None)
    for d in range(days):
        console.print(f"[bold cyan]── cycle {d+1}/{days} ──[/bold cyan]")
        result = paper.step(cfg, portfolio, tickers)
        for line in result.closed:
            console.print(f"  [red]CLOSED[/red] {line}")
        for line in result.opened:
            console.print(f"  [green]OPENED[/green] {line}")
        console.print(f"  [dim]equity ${result.equity:,.2f}[/dim]")
    render_portfolio(cfg, portfolio)


@paper_cmd.command("open")
@click.argument("ticker")
@click.option("--qty", type=float, required=True)
@click.option("--price", type=float, required=True)
@click.option("--direction", type=click.Choice(["long", "short"]), default="long")
@click.option("--instrument", type=click.Choice(["equity", "call", "put"]), default="equity")
@click.option("--stop", type=float, default=None)
@click.option("--target", type=float, default=None)
@click.pass_context
def paper_open(ctx, ticker, qty, price, direction, instrument, stop, target):
    """Manually open a paper position."""
    portfolio = ctx.obj["portfolio"]
    pid = portfolio.open_position(
        ticker=ticker.upper(),
        instrument=instrument,
        direction=direction,
        quantity=qty,
        entry_price=price,
        stop_price=stop,
        target_price=target,
        notes="manual",
    )
    console.print(f"[green]Opened #{pid}[/green] {ticker} {instrument} {direction} qty {qty} @ {price}")


@paper_cmd.command("close")
@click.argument("position_id", type=int)
@click.option("--price", type=float, required=True)
@click.pass_context
def paper_close(ctx, position_id, price):
    """Manually close a paper position by id."""
    portfolio = ctx.obj["portfolio"]
    realized = portfolio.close_position(position_id, price, notes="manual")
    console.print(f"[red]Closed #{position_id}[/red] @ {price} — realized {realized:+,.2f}")


@cli.command()
@click.option("--show-closed/--no-show-closed", default=True)
@click.pass_context
def report(ctx: click.Context, show_closed: bool) -> None:
    """Print portfolio status, open positions, target tracking, and recent trades."""
    cfg = ctx.obj["cfg"]
    portfolio = ctx.obj["portfolio"]
    render_portfolio(cfg, portfolio)
    if show_closed:
        render_closed(portfolio)


@cli.command()
@click.pass_context
def backtest(ctx: click.Context) -> None:
    """(stub) Historical backtest. Coming soon."""
    console.print("[yellow]Backtester stub — not implemented yet.[/yellow]")


if __name__ == "__main__":
    cli(obj={})
