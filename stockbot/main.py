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


@paper_cmd.command("convict")
@click.option("--watchlist", "-w", help="Comma-separated tickers; overrides config.")
@click.pass_context
def paper_convict(ctx: click.Context, watchlist: str | None) -> None:
    """Run the conviction gate over current ideas; persist verdicts (roadmap §13.1).

    Per-ticker pipeline: price history → TechnicalRead → setup match
    → fundamentals → assembler → evaluate → log. `factor_composite`
    is still None (Cluster 4 wires it); `setup_validated` will fail
    closed for any setup whose walk-forward expectancy hasn't been
    recorded in `setup_performance` yet (Cluster 2 / step 2D).
    """
    from .data import macro as macro_data
    from .data import prices as price_data
    from .data.fundamentals import get_fundamentals
    from .ops.config_snapshot import hash_config, snapshot as snap_config
    from .ops.conviction_log import log_evaluation
    from .strategy.conviction import evaluate
    from .strategy.conviction_context import build_context
    from .strategy.scorer import read_technicals

    cfg = ctx.obj["cfg"]
    tickers = resolve_universe(cfg, watchlist.split(",") if watchlist else None)
    console.print(f"[dim]Scanning {len(tickers)} tickers…[/dim]")
    ideas = generate_ideas(cfg, tickers)
    if not ideas:
        console.print("[yellow]No ideas above threshold to gate.[/yellow]")
        return

    snap_config(cfg)
    cfg_hash = hash_config(cfg)
    macro = macro_data.snapshot()
    console.print(f"[dim]Macro: VIX={macro.vix} curve(2s10s)={macro.yield_curve_2s10s}bps[/dim]")

    # Cache per-ticker reads so options/ETF ideas for the same underlying don't refetch.
    tech_cache: dict[str, object] = {}
    fund_cache: dict[str, object] = {}

    n_pass = 0
    for idea in ideas:
        if idea.ticker not in tech_cache:
            df = price_data.get_history(idea.ticker, period="6mo", interval="1d")
            tech_cache[idea.ticker] = read_technicals(df, cfg)
            fund_cache[idea.ticker] = get_fundamentals(idea.ticker)
        gate_ctx = build_context(
            cfg, idea, macro=macro,
            tech=tech_cache[idea.ticker],
            fundamentals=fund_cache[idea.ticker],
        )
        pick, verdicts = evaluate(idea, gate_ctx)
        log_evaluation(idea, verdicts, pick, config_hash=cfg_hash)
        marker = "[green]PASS[/green]" if pick else "[red]FAIL[/red]"
        setup_note = ""
        if gate_ctx.matched_setup is not None:
            setup_note = f" [dim]matched={gate_ctx.matched_setup.name}[/dim]"
        console.print(f"{marker} {idea.ticker:<6} score={idea.score:+.2f}{setup_note}")
        for name, result in verdicts.items():
            tick = "[green]✓[/green]" if result.passed else "[red]✗[/red]"
            console.print(f"   {tick} {name:<18} {result.reason}")
        if pick:
            n_pass += 1
    console.print(f"\n[bold]{n_pass}/{len(ideas)}[/bold] passed all gates. "
                  f"Audit rows persisted to conviction_log.")


@paper_cmd.command("watch")
@click.option("--watchlist", "-w", help="Comma-separated tickers; overrides config.")
@click.option("--once", is_flag=True, help="Run a single pass and exit (good for cron).")
@click.option("--max-cycles", type=int, default=None,
              help="Stop after this many cycles (default: run forever).")
@click.pass_context
def paper_watch(ctx: click.Context, watchlist: str | None,
                once: bool, max_cycles: int | None) -> None:
    """Conviction gate + notification dispatch on a cadence (roadmap §13.3).

    Continuously: scan → gate → notify, sleeping `notifications.cadence_minutes`
    between cycles. Ctrl-C to exit cleanly. With --once, runs a single pass.
    """
    from .ops.scheduler import run_single_pass, run_watch_loop

    cfg = ctx.obj["cfg"]
    portfolio = ctx.obj["portfolio"]
    tickers = resolve_universe(cfg, watchlist.split(",") if watchlist else None)

    if once:
        result = run_single_pass(cfg, portfolio, tickers)
        console.print(
            f"[bold]ideas={result.n_ideas}[/bold] · "
            f"passes={result.n_passes} · "
            f"[green]dispatched={result.n_dispatched}[/green] · "
            f"[yellow]suppressed={result.n_suppressed}[/yellow]"
        )
        for s in result.suppressions:
            console.print(f"  [dim]suppressed:[/dim] {s}")
        return

    console.print("[dim]Press Ctrl-C to exit cleanly.[/dim]")
    run_watch_loop(cfg, portfolio, tickers, max_cycles=max_cycles)


@paper_cmd.command("ack")
@click.argument("notification_id", type=int)
@click.pass_context
def paper_ack(ctx: click.Context, notification_id: int) -> None:
    """Acknowledge a notification (roadmap §13.3)."""
    from .ops.notification_log import ack
    if ack(notification_id):
        console.print(f"[green]Acked #{notification_id}[/green]")
    else:
        console.print(f"[red]No notification with id {notification_id}[/red]")


@paper_cmd.command("snooze")
@click.argument("notification_id", type=int)
@click.option("--hours", type=float, required=True, help="Snooze duration in hours.")
@click.pass_context
def paper_snooze(ctx: click.Context, notification_id: int, hours: float) -> None:
    """Snooze the notification's ticker for N hours (roadmap §13.3).

    Resurfaces early only if the score moves by more than
    `notifications.resurface_score_delta` while the snooze is active.
    """
    from .ops.notification_log import snooze
    if snooze(notification_id, hours):
        console.print(f"[green]Snoozed #{notification_id} for {hours}h[/green]")
    else:
        console.print(f"[red]No notification with id {notification_id}[/red]")


@paper_cmd.command("convict-log")
@click.option("--limit", "-n", type=int, default=20, show_default=True,
              help="How many rows to show.")
@click.option("--ticker", "-t", help="Filter to one ticker.")
@click.pass_context
def paper_convict_log(ctx: click.Context, limit: int, ticker: str | None) -> None:
    """Show the most recent conviction_log rows (roadmap §13.1)."""
    from rich.table import Table

    from .ops.conviction_log import recent

    rows = recent(limit=limit * 4 if ticker else limit)
    if ticker:
        rows = [r for r in rows if r["ticker"] == ticker.upper()][:limit]
    if not rows:
        console.print("[yellow]No conviction_log rows yet. Run `paper convict` first.[/yellow]")
        return

    table = Table(title="conviction_log (newest first)")
    table.add_column("id", justify="right", style="dim")
    table.add_column("ts", style="dim")
    table.add_column("ticker")
    table.add_column("score", justify="right")
    table.add_column("verdict")
    table.add_column("gates s|f|r|v|c|d")  # score | factor | regime | setup_validated | cooldown | data_quality

    for r in rows:
        verdict = "[green]PASS[/green]" if r["overall_passed"] else "[red]FAIL[/red]"
        flags = "".join(
            "[green]✓[/green]" if r[f"{name}_passed"] else "[red]✗[/red]"
            for name in ("score", "factor_agreement", "regime",
                        "setup_validated", "cooldown", "data_quality")
        )
        table.add_row(
            str(r["id"]), r["ts"][:19], r["ticker"],
            f"{r['score']:+.2f}", verdict, flags,
        )
    console.print(table)


@paper_cmd.command("rebalance")
@click.option("--watchlist", "-w", help="Comma-separated tickers; overrides config.")
@click.pass_context
def paper_rebalance(ctx: click.Context, watchlist: str | None) -> None:
    """Fire a book-level rebalance proposal (roadmap §13.8).

    Writes one row to rebalance_proposals as 'pending' and prints its id.
    Approve with `paper rebalance-approve <id>` or reject with
    `paper rebalance-reject <id>`. Translating an approved proposal into
    paper orders is the next user-driven step.
    """
    from .engine.paper import run_rebalance

    cfg = ctx.obj["cfg"]
    portfolio = ctx.obj["portfolio"]
    tickers = resolve_universe(cfg, watchlist.split(",") if watchlist else None)
    proposal_id = run_rebalance(cfg, portfolio, tickers)
    if proposal_id is None:
        console.print(
            "[yellow]Rebalancer skipped — either disabled in config "
            "(`rebalance.enabled: false`) or nothing to rebalance.[/yellow]"
        )
        return
    console.print(f"[green]Proposal #{proposal_id} recorded as pending.[/green]")
    console.print("[dim]View with `paper rebalance-log`.[/dim]")


@paper_cmd.command("rebalance-approve")
@click.argument("proposal_id", type=int)
@click.pass_context
def paper_rebalance_approve(ctx: click.Context, proposal_id: int) -> None:
    """Approve a pending rebalance proposal."""
    from .ops import rebalance as ops_rebalance

    if ops_rebalance.approve(proposal_id):
        console.print(f"[green]Proposal #{proposal_id} approved.[/green]")
    else:
        console.print(
            f"[red]No pending proposal #{proposal_id} — already decided, or unknown id.[/red]"
        )


@paper_cmd.command("rebalance-reject")
@click.argument("proposal_id", type=int)
@click.pass_context
def paper_rebalance_reject(ctx: click.Context, proposal_id: int) -> None:
    """Reject a pending rebalance proposal."""
    from .ops import rebalance as ops_rebalance

    if ops_rebalance.reject(proposal_id):
        console.print(f"[yellow]Proposal #{proposal_id} rejected.[/yellow]")
    else:
        console.print(
            f"[red]No pending proposal #{proposal_id} — already decided, or unknown id.[/red]"
        )


@paper_cmd.command("rebalance-log")
@click.option("--limit", "-n", type=int, default=20, show_default=True)
@click.option("--pending-only", is_flag=True, default=False, help="Show only pending proposals.")
@click.pass_context
def paper_rebalance_log(ctx: click.Context, limit: int, pending_only: bool) -> None:
    """Show recent rebalance proposals."""
    from rich.table import Table

    from .ops import rebalance as ops_rebalance

    rows = ops_rebalance.pending() if pending_only else ops_rebalance.recent(limit=limit)
    if not rows:
        console.print(
            "[yellow]No rebalance proposals yet. "
            "Enable `rebalance.enabled: true` and run `paper rebalance`.[/yellow]"
        )
        return

    table = Table(title="rebalance_proposals")
    table.add_column("id", justify="right", style="dim")
    table.add_column("ts", style="dim")
    table.add_column("algo")
    table.add_column("status")
    table.add_column("turnover", justify="right")
    table.add_column("feasible")
    table.add_column("grows / shrinks")

    for r in rows:
        changes = r.get("changes", [])
        grows = sum(
            1 for c in changes
            if abs(c["current_weight"]) < 1e-6 and abs(c["target_weight"]) > 1e-6
            or c["target_weight"] > c["current_weight"] + 1e-6
        )
        shrinks = sum(
            1 for c in changes
            if abs(c["target_weight"]) < 1e-6 and abs(c["current_weight"]) > 1e-6
            or c["target_weight"] < c["current_weight"] - 1e-6
        )
        status_style = {
            "pending": "[yellow]pending[/yellow]",
            "approved": "[green]approved[/green]",
            "rejected": "[red]rejected[/red]",
        }.get(r["status"], r["status"])
        feas = "[green]✓[/green]" if r["feasible"] else "[red]✗[/red]"
        table.add_row(
            str(r["id"]), (r["ts"] or "")[:19], r["algo"], status_style,
            f"{r['capped_turnover']:.2%}", feas, f"{grows} / {shrinks}",
        )
    console.print(table)


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
@click.option("--start", required=True, help="Backtest start date YYYY-MM-DD.")
@click.option("--end", required=True, help="Backtest end date YYYY-MM-DD.")
@click.option("--watchlist", "-w", help="Comma-separated tickers; overrides config.")
@click.option("--rebalance-days", type=int, default=5, show_default=True)
@click.option("--starting-cash", type=float, default=100_000.0, show_default=True)
@click.pass_context
def backtest(ctx: click.Context, start: str, end: str, watchlist: str | None,
             rebalance_days: int, starting_cash: float) -> None:
    """Run a walk-forward-ready backtest over the watchlist using the factor model.

    Records the matched setup at each entry and, on completion, upserts
    one `setup_performance` row per observed setup. The conviction
    gate's setup_validated check reads from that table (roadmap §13.2).
    """
    from datetime import datetime

    from .backtest import Backtester
    from .backtest.setup_aggregator import aggregate_setup_performance, persist
    from .data import prices as price_data
    from .data.fundamentals import get_fundamentals
    from .data.macro import MacroSnapshot
    from .strategy.factors import FactorWeights, score_universe
    from .strategy.scorer import read_technicals
    from .strategy.setups.matcher import match

    cfg = ctx.obj["cfg"]
    tickers = resolve_universe(cfg, watchlist.split(",") if watchlist else None)
    console.print(f"[dim]Loading price history for {len(tickers)} tickers…[/dim]")
    history = {t: price_data.get_history(t, period="5y", interval="1d") for t in tickers}
    fundamentals = {t: get_fundamentals(t) for t in tickers}

    # Match-at-entry closure. Uses a neutral macro snapshot; a true
    # point-in-time macro feed is its own (future) data wiring problem.
    # Returning the first match by registration order is fine here —
    # we are *generating* perf data, so there's no perf to consult yet.
    neutral_macro = MacroSnapshot(
        vix=20.0, vix_3m=20.0, yield_2y=4.0, yield_10y=4.0, yield_30y=4.0,
        dxy=100.0, spx_close=4500.0, yield_curve_2s10s=0.0, vix_term_structure=1.0,
    )

    def match_fn(ticker, sliced_df):
        tech = read_technicals(sliced_df, cfg)
        if tech is None:
            return None
        matches = match(tech, fundamentals.get(ticker), None, neutral_macro)
        return matches[0].name if matches else None

    bt = Backtester(
        history, starting_cash=starting_cash, rebalance_freq=rebalance_days,
        match_fn=match_fn,
    )

    weights = FactorWeights()

    def signal_fn(asof, sliced_history):
        # Build target weights from current factor composite. Long top-quintile only.
        report = score_universe(tickers, fundamentals, sliced_history, weights=weights)
        ranked = sorted(report.composite.items(), key=lambda kv: kv[1], reverse=True)
        n_long = max(1, len(ranked) // 5)
        positive = [(t, s) for t, s in ranked[:n_long] if s > 0]
        if not positive:
            return {}
        weight = 1.0 / len(positive)
        return {t: weight for t, _ in positive}

    result = bt.run(signal_fn, start=start, end=end)
    console.print(f"[bold]{result.metrics.headline()}[/bold]")
    console.print(f"trades: {len(result.trades)} (closed {sum(1 for t in result.trades if t.closed)})")

    # Walk-forward → setup_performance.
    perf_rows = aggregate_setup_performance(result.trades, as_of=datetime.utcnow())
    if perf_rows:
        persist(perf_rows)
        console.print(f"\n[dim]Upserted {len(perf_rows)} setup_performance rows.[/dim]")
        for p in sorted(perf_rows, key=lambda r: r.expectancy, reverse=True):
            console.print(
                f"  [bold]{p.setup_name:<28}[/bold] n={p.n_trades:<4} "
                f"win={p.win_rate:.0%} avg_r={p.avg_r:+.3f} "
                f"E={p.expectancy:+.3f} sharpe={p.sharpe:+.2f}"
            )
    else:
        console.print("[yellow]No trades carried a setup_name; setup_performance untouched.[/yellow]")


@cli.command(name="risk-report")
@click.pass_context
def risk_report(ctx: click.Context) -> None:
    """Show VaR/CVaR and portfolio Greeks for current positions."""
    from .data import prices as price_data
    from .portfolio.greeks_agg import aggregate_greeks
    from .portfolio.var import historical_var, parametric_var, portfolio_returns

    cfg = ctx.obj["cfg"]
    portfolio = ctx.obj["portfolio"]
    open_pos = portfolio.list_open()
    if not open_pos:
        console.print("[yellow]No open positions.[/yellow]")
        return

    # Build per-ticker price history and weights.
    mark_prices = {p.ticker: price_data.get_last_price(p.ticker) or p.entry_price for p in open_pos}
    equity = portfolio.equity(mark_prices)
    positions_for_var = []
    history = {}
    for p in open_pos:
        weight = (p.market_value(mark_prices[p.ticker]) / equity) if equity > 0 else 0.0
        positions_for_var.append({"ticker": p.ticker, "weight": weight})
        history[p.ticker] = price_data.get_history(p.ticker, period="2y", interval="1d")
    rets = portfolio_returns(positions_for_var, history)
    par = parametric_var(rets, equity)
    hist = historical_var(rets, equity)
    console.print(f"[bold]VaR (1-day, parametric)[/bold] 95%: ${par.var_95:,.0f}  99%: ${par.var_99:,.0f}")
    console.print(f"[bold]CVaR (1-day, parametric)[/bold] 95%: ${par.cvar_95:,.0f}  99%: ${par.cvar_99:,.0f}")
    console.print(f"[bold]VaR (1-day, historical)[/bold] 95%: ${hist.var_95:,.0f}  99%: ${hist.var_99:,.0f}")

    g = aggregate_greeks(portfolio)
    console.print(
        f"[bold]Portfolio Greeks[/bold] Δ {g.delta:+,.1f}  Γ {g.gamma:+,.2f}  "
        f"Θ {g.theta:+,.0f}/d  ν {g.vega:+,.0f}/1%  ρ {g.rho:+,.0f}/1%"
    )
    for b in g.thresholds_breached:
        console.print(f"  [red]⚠ {b}[/red]")


@cli.command(name="rec-log")
@click.option("--limit", type=int, default=25, show_default=True)
@click.pass_context
def rec_log(ctx: click.Context, limit: int) -> None:
    """Show recent recommendations (executed or not)."""
    from .ops.recommendation_log import recent, hit_rate

    rows = recent(limit)
    if not rows:
        console.print("[yellow]No recommendations logged yet.[/yellow]")
        return
    for r in rows:
        flag = "[green]EXEC[/green]" if r.get("executed") else "[dim]SKIP[/dim]"
        console.print(
            f"#{r['id']} {r['ts']} {flag} {r['ticker']} {r['instrument']} "
            f"{r['direction']} score {r.get('score', 0.0):+.2f}"
        )
    rate = hit_rate(limit)
    console.print(
        f"[bold]Execution rate[/bold]: {rate['executed']}/{rate['total']} "
        f"recs placed ({rate['rate']*100:.0f}%) | avg score {rate['avg_score']:+.2f}"
    )
    console.print(
        "[dim](Skips reflect guardrails, sizing limits, or insufficient cash — "
        "logging every recommendation is required by the audit policy.)[/dim]"
    )


@cli.command(name="stress")
@click.pass_context
def stress_cmd(ctx: click.Context) -> None:
    """Replay current portfolio composition through historical stress regimes."""
    from .data import prices as price_data
    from .portfolio.stress import stress_test

    portfolio = ctx.obj["portfolio"]
    open_pos = portfolio.list_open()
    if not open_pos:
        console.print("[yellow]No open positions to stress.[/yellow]")
        return
    mark_prices = {p.ticker: price_data.get_last_price(p.ticker) or p.entry_price for p in open_pos}
    equity = portfolio.equity(mark_prices)
    positions = [
        {
            "ticker": p.ticker,
            "weight": (p.market_value(mark_prices[p.ticker]) / equity) if equity > 0 else 0.0,
        }
        for p in open_pos
    ]
    history = {p.ticker: price_data.get_history(p.ticker, period="max", interval="1d") for p in open_pos}
    results = stress_test(positions, history)
    for r in results:
        console.print(
            f"[bold]{r.regime}[/bold] [{r.start} → {r.end}]  "
            f"ret {r.portfolio_return*100:+.1f}%  worstDay {r.worst_day*100:+.1f}%  DD {r.drawdown*100:.1f}%"
        )


@cli.command()
@click.argument("ticker")
@click.option("--no-factors", is_flag=True, help="Skip the slower factor-model breakdown.")
@click.pass_context
def explain(ctx: click.Context, ticker: str, no_factors: bool) -> None:
    """Show the full breakdown behind a ticker's composite score."""
    from .strategy.score_explain import explain_score

    cfg = ctx.obj["cfg"]
    b = explain_score(cfg, ticker, include_factors=not no_factors)
    if b is None:
        console.print(f"[yellow]Not enough price history to score {ticker.upper()}.[/yellow]")
        return

    color = "green" if b.classification == "LONG" else "red" if b.classification == "SHORT" else "yellow"
    console.print(
        f"[bold]{b.ticker}[/bold]  last ${b.last_price:,.2f}  "
        f"score [bold {color}]{b.final_score:+.3f}[/bold {color}]  → {b.classification}"
    )
    console.print(
        f"  thresholds: long ≥ {b.thresholds['min_long']:+.2f}, "
        f"short ≤ {b.thresholds['min_short']:+.2f}"
    )

    console.print("\n[bold]Technical sub-signals[/bold] (inside the technical block)")
    for c in b.tech_components:
        console.print(
            f"  {c.name:<10} value {c.value:+.3f}  × weight {c.weight:.2f}  "
            f"= contribution {c.contribution:+.3f}"
        )
    console.print(f"  → tech_composite = {b.tech_composite:+.3f}")

    console.print(
        f"\n[bold]Sentiment[/bold]  net {b.sentiment_net:+.2f}  "
        f"confidence {b.sentiment_confidence:.2f}  "
        f"subscore {b.sentiment_subscore:+.3f}  "
        f"(blend weight {b.sentiment_weight:.2f})"
    )

    console.print("\n[bold]Final blend[/bold]")
    for c in b.blended_components:
        console.print(
            f"  {c.name:<18} value {c.value:+.3f}  × weight {c.weight:.2f}  "
            f"= contribution {c.contribution:+.3f}"
        )
    for line in b.formula().split("\n"):
        console.print(f"  [dim]{line}[/dim]")

    console.print("\n[bold]Indicator readings[/bold]")
    i = b.indicators
    console.print(
        f"  RSI(14) {i.rsi_14:.1f} ({i.rsi_label()})  "
        f"MACD hist {i.macd_hist_last:+.3f}  "
        f"SMA20 {i.sma_20:.2f}  SMA50 {i.sma_50:.2f}  "
        f"trend: {i.trend_label()}"
    )
    console.print(
        f"  Bollinger: [{i.bollinger_lower:.2f} – {i.bollinger_upper:.2f}]  "
        f"position {i.bollinger_pct*100:.0f}% of band  "
        f"avg-vol-20d {i.avg_volume_20:,.0f}"
    )

    if b.factor_scores:
        console.print("\n[bold]Factor model (parallel cross-section signal)[/bold]")
        for name, val in b.factor_scores.items():
            console.print(f"  {name:<10} {val:+.3f}")
        if b.factor_composite is not None:
            console.print(f"  → factor_composite = {b.factor_composite:+.3f}")

    if b.reasons:
        console.print("\n[bold]Reasons[/bold]")
        for r in b.reasons:
            console.print(f"  • {r}")

    if b.warnings:
        console.print("\n[bold yellow]Warnings[/bold yellow]")
        for w in b.warnings:
            console.print(f"  ⚠ {w}")


if __name__ == "__main__":
    cli(obj={})
