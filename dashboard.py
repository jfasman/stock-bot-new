"""Streamlit dashboard for stock-bot.

Run:
    streamlit run dashboard.py
"""
from __future__ import annotations

import time
from datetime import datetime

import pandas as pd
import streamlit as st

from stockbot.config import load_config
from stockbot.data import options as opt_data
from stockbot.data import prices as price_data
from stockbot.data.universe import resolve_universe
from stockbot.engine import paper as engine
from stockbot.portfolio.portfolio import Portfolio, Position
from stockbot.portfolio.targets import evaluate as evaluate_target
from stockbot.strategy.ideas import generate_ideas

st.set_page_config(
    page_title="stock-bot dashboard",
    page_icon=":chart_with_upwards_trend:",
    layout="wide",
)


@st.cache_resource
def _config():
    return load_config()


@st.cache_resource
def _portfolio():
    cfg = _config()
    return Portfolio(starting_cash=float(cfg.portfolio.get("starting_cash", 100_000)))


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


def _open_positions_df(portfolio: Portfolio) -> tuple[pd.DataFrame, dict[str, float]]:
    rows = []
    marks: dict[str, float] = {}
    for p in portfolio.list_open():
        mark = _mark(p) or p.entry_price
        marks[p.option_symbol or p.ticker] = mark
        pnl = p.unrealized_pnl(mark)
        cost = p.cost_basis
        pnl_pct = (pnl / cost * 100) if cost else 0.0
        type_label = p.instrument.upper()
        if p.instrument != "equity":
            strike = f"{p.option_strike:.1f}" if p.option_strike else "?"
            exp = p.option_expiration or "?"
            type_label = f"{p.instrument.upper()} {strike} {exp}"
        rows.append({
            "ID": p.id,
            "Ticker": p.ticker,
            "Type": type_label,
            "Dir": p.direction,
            "Qty": p.quantity,
            "Entry": round(p.entry_price, 2),
            "Mark": round(mark, 2),
            "P&L $": round(pnl, 2),
            "P&L %": round(pnl_pct, 2),
            "Stop": p.stop_price,
            "Target": p.target_price,
            "Notes": p.notes or "",
        })
    df = pd.DataFrame(rows)
    return df, marks


def _closed_positions_df(portfolio: Portfolio) -> pd.DataFrame:
    rows = []
    for p in portfolio.list_closed():
        rows.append({
            "ID": p.id,
            "Ticker": p.ticker,
            "Type": p.instrument,
            "Dir": p.direction,
            "Qty": p.quantity,
            "Entry": round(p.entry_price, 2),
            "Exit": round(p.exit_price, 2) if p.exit_price else None,
            "Realized $": round(p.realized_pnl or 0, 2),
            "Entry Date": p.entry_date[:10],
            "Exit Date": (p.exit_date or "")[:10],
            "Notes": p.notes or "",
        })
    return pd.DataFrame(rows)


def _equity_curve_df(portfolio: Portfolio) -> pd.DataFrame:
    rows = portfolio.equity_curve()
    if not rows:
        return pd.DataFrame(columns=["ts", "equity"])
    df = pd.DataFrame(rows, columns=["ts", "equity"])
    df["ts"] = pd.to_datetime(df["ts"])
    return df


def _recommendations_df(limit: int = 50) -> tuple[pd.DataFrame, dict]:
    from stockbot.ops.recommendation_log import hit_rate, recent
    rows = recent(limit)
    if not rows:
        return pd.DataFrame(), {"executed": 0, "total": 0, "rate": 0.0, "avg_score": 0.0}
    out = []
    for r in rows:
        out.append({
            "#": r["id"],
            "Time": (r["ts"] or "")[:19],
            "Status": "EXEC" if r.get("executed") else "SKIP",
            "Ticker": r["ticker"],
            "Instrument": r["instrument"],
            "Dir": r["direction"],
            "Score": round(r.get("score") or 0.0, 2),
            "Suggested wt": round(r.get("suggested_weight") or 0.0, 3),
            "Stop": r.get("stop_price"),
            "Target": r.get("target_price"),
            "Invalidation": r.get("invalidation") or "",
            "Config hash": (r.get("config_hash") or "")[:10],
        })
    return pd.DataFrame(out), hit_rate(limit)


def _risk_panel(portfolio: Portfolio) -> None:
    from stockbot.data import prices as price_data
    from stockbot.portfolio.greeks_agg import aggregate_greeks
    from stockbot.portfolio.var import historical_var, parametric_var, portfolio_returns

    open_pos = portfolio.list_open()
    if not open_pos:
        st.info("No open positions to analyze. Run a paper cycle first.")
        return
    mark_prices = {p.ticker: price_data.get_last_price(p.ticker) or p.entry_price for p in open_pos}
    equity = portfolio.equity(mark_prices)
    positions_for_var = [
        {
            "ticker": p.ticker,
            "weight": (p.market_value(mark_prices[p.ticker]) / equity) if equity > 0 else 0.0,
        }
        for p in open_pos
    ]
    history = {p.ticker: price_data.get_history(p.ticker, period="2y", interval="1d") for p in open_pos}
    rets = portfolio_returns(positions_for_var, history)
    par = parametric_var(rets, equity)
    hist = historical_var(rets, equity)

    st.subheader("Value-at-Risk (1-day)")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("VaR 95% (parametric)", f"${par.var_95:,.0f}")
    c2.metric("VaR 99% (parametric)", f"${par.var_99:,.0f}")
    c3.metric("CVaR 95% (parametric)", f"${par.cvar_95:,.0f}")
    c4.metric("CVaR 99% (parametric)", f"${par.cvar_99:,.0f}")
    c5, c6 = st.columns(2)
    c5.metric("VaR 95% (historical)", f"${hist.var_95:,.0f}")
    c6.metric("VaR 99% (historical)", f"${hist.var_99:,.0f}")
    st.caption(
        "Parametric assumes Gaussian returns; historical uses the empirical tail. "
        "Show both because real tails are fatter than the Gaussian implies."
    )

    g = aggregate_greeks(portfolio)
    st.subheader("Portfolio Greeks")
    gc = st.columns(5)
    gc[0].metric("Δ (delta)", f"{g.delta:+,.1f}", help="Net directional exposure in $ per 1pt move")
    gc[1].metric("Γ (gamma)", f"{g.gamma:+,.2f}", help="Convexity — Δ change per 1pt move")
    gc[2].metric("Θ (theta)", f"{g.theta:+,.0f}/d", help="Time decay per day, $")
    gc[3].metric("ν (vega)", f"{g.vega:+,.0f}/1%", help="Sensitivity to a 1 vol pt move")
    gc[4].metric("ρ (rho)", f"{g.rho:+,.0f}/1%", help="Sensitivity to a 1% rates move")
    if g.thresholds_breached:
        for b in g.thresholds_breached:
            st.warning(f"⚠ {b}")
    else:
        st.success("All Greek thresholds within configured limits.")


def _stress_panel(portfolio: Portfolio) -> None:
    from stockbot.data import prices as price_data
    from stockbot.portfolio.stress import stress_test

    open_pos = portfolio.list_open()
    if not open_pos:
        st.info("No open positions to stress.")
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
    with st.spinner("Replaying portfolio through historical regimes…"):
        results = stress_test(positions, history)
    rows = [
        {
            "Regime": r.regime,
            "Window": f"{r.start} → {r.end}",
            "Portfolio return": f"{r.portfolio_return*100:+.1f}%",
            "Worst day": f"{r.worst_day*100:+.1f}%",
            "Max drawdown": f"{r.drawdown*100:.1f}%",
        }
        for r in results
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    st.caption(
        "Each row replays *today's* portfolio weights through the actual price "
        "series of that historical regime. Not a forecast — a sanity check on "
        "what tail conditions would do to this book."
    )


def _score_panel(cfg, watchlist_tickers: list[str]) -> None:
    from stockbot.strategy.score_explain import explain_score

    st.markdown(
        "**How is the score built?** The dashboard score blends technicals "
        "(trend / momentum / breakout) and sentiment into a signed number "
        "in [−1, +1]. Pick a ticker to see every sub-signal, its weight, and "
        "the exact arithmetic that produced the final number — the same math "
        "the system uses to rank ideas in production."
    )
    universe = sorted(set(watchlist_tickers))
    if not universe:
        st.info("Add tickers to the sidebar watchlist to use this tab.")
        return

    col1, col2 = st.columns([3, 1])
    ticker = col1.selectbox(
        "Ticker", universe, key="score_ticker",
        help="Pulls from the sidebar watchlist."
    )
    skip_factors = col2.checkbox(
        "Skip factor model", value=False,
        help="The factor model (value/momentum/quality/lowvol/size/meanrev) "
             "is a parallel cross-sectional signal. Skipping speeds up the render.",
    )

    with st.spinner(f"Re-running scorer for {ticker}…"):
        b = explain_score(cfg, ticker, include_factors=not skip_factors)
    if b is None:
        st.warning(f"Not enough price history to score {ticker}.")
        return

    cls_color = {"LONG": "green", "SHORT": "red", "NEUTRAL": "orange"}[b.classification]
    sc1, sc2, sc3, sc4 = st.columns(4)
    sc1.metric(f"{b.ticker} score", f"{b.final_score:+.3f}", b.classification)
    sc2.metric("Last price", f"${b.last_price:,.2f}")
    sc3.metric("Long threshold", f"≥ {b.thresholds['min_long']:+.2f}")
    sc4.metric("Short threshold", f"≤ {b.thresholds['min_short']:+.2f}")

    st.subheader("Technical sub-signals")
    st.caption(
        "These three combine inside the technical block. Hard-coded weights: "
        "trend 0.50, momentum 0.30, breakout 0.20."
    )
    tech_df = pd.DataFrame([
        {
            "Signal": c.name,
            "Value (−1 → +1)": round(c.value, 3),
            "Weight": c.weight,
            "Contribution": round(c.contribution, 3),
        }
        for c in b.tech_components
    ])
    st.dataframe(tech_df, use_container_width=True, hide_index=True)
    st.bar_chart(tech_df.set_index("Signal")["Contribution"])
    st.caption(f"→ technical composite = **{b.tech_composite:+.3f}**")

    st.subheader("Sentiment")
    s1, s2, s3 = st.columns(3)
    s1.metric("Net sentiment", f"{b.sentiment_net:+.2f}", help="Average bullish minus bearish across mentions")
    s2.metric("Confidence", f"{b.sentiment_confidence:.2f}", help="Volume- and recency-weighted")
    s3.metric("Sub-score", f"{b.sentiment_subscore:+.3f}", help="net × confidence — what feeds the blend")

    st.subheader("Final blend")
    st.caption(
        f"Outer blend weights are configurable. Current sentiment weight: "
        f"**{b.sentiment_weight:.2f}** (set in `config.yaml` → `strategy.sentiment_weight`)."
    )
    blend_df = pd.DataFrame([
        {
            "Block": c.name,
            "Value": round(c.value, 3),
            "Weight": c.weight,
            "Contribution": round(c.contribution, 3),
        }
        for c in b.blended_components
    ])
    st.dataframe(blend_df, use_container_width=True, hide_index=True)
    st.bar_chart(blend_df.set_index("Block")["Contribution"])
    st.code(b.formula(), language="text")

    st.subheader("Indicator readings")
    i = b.indicators
    ic1, ic2, ic3 = st.columns(3)
    ic1.metric("RSI(14)", f"{i.rsi_14:.1f}", i.rsi_label())
    ic2.metric("MACD histogram", f"{i.macd_hist_last:+.3f}", "bullish" if i.macd_hist_last > 0 else "bearish")
    ic3.metric("Bollinger position", f"{i.bollinger_pct*100:.0f}% of band",
               help="0% = at lower band, 100% = at upper band")
    ic4, ic5, ic6 = st.columns(3)
    ic4.metric("SMA(20)", f"${i.sma_20:.2f}")
    ic5.metric("SMA(50)", f"${i.sma_50:.2f}")
    ic6.metric("Avg vol (20d)", f"{i.avg_volume_20:,.0f}")
    st.caption(f"Trend pattern: **{i.trend_label()}**")

    st.subheader("Matched setups")
    st.caption(
        "Setups whose pattern fits the current technical state. Each row is "
        "from the named library in `strategy/setups/`; the conviction gate's "
        "`setup_validated` check picks the highest-expectancy match. "
        "Expectancy and trade count come from the walk-forward run that "
        "populated `setup_performance` (run `stockbot backtest …`)."
    )
    if not b.matched_setups:
        st.info("No setup currently matches this ticker's technical state.")
    else:
        ms_rows = []
        for m in b.matched_setups:
            ms_rows.append({
                "Setup": m.name,
                "Direction": m.direction,
                "Hold (days)": f"{m.expected_holding_days[0]}-{m.expected_holding_days[1]}",
                "Validated": "yes" if m.has_performance else "no",
                "n": m.n_trades,
                "Win rate": f"{m.win_rate*100:.0f}%" if m.has_performance else "—",
                "Expectancy": f"{m.expectancy:+.3f}" if m.has_performance else "—",
                "Last validated": (m.last_validated_at or "—")[:10],
            })
        st.dataframe(pd.DataFrame(ms_rows), use_container_width=True, hide_index=True)

    if b.factor_scores:
        st.subheader("Factor model breakdown (parallel signal)")
        st.caption(
            "Six classical factors scored from fundamentals and price history. "
            "Not currently fused into the dashboard score — runs alongside as a "
            "sanity check."
        )
        fact_df = pd.DataFrame([
            {"Factor": k, "Score": round(v, 3)} for k, v in b.factor_scores.items()
        ])
        st.dataframe(fact_df, use_container_width=True, hide_index=True)
        if b.factor_composite is not None:
            st.metric("Factor composite", f"{b.factor_composite:+.3f}")

    if b.reasons:
        st.subheader("Plain-English reasons")
        for r in b.reasons:
            st.markdown(f"- {r}")

    if b.warnings:
        for w in b.warnings:
            st.warning(w)


def _conviction_panel() -> None:
    """Conviction-gate audit + setup performance. Spec: roadmap §13.1 + §13.2."""
    import json

    from stockbot.ops.conviction_log import recent as recent_convictions
    from stockbot.ops.setup_performance import all_performance

    st.markdown(
        "**The conviction gate is the second, stricter layer between "
        "*idea generated* and *user alerted*.** Every evaluation — pass or "
        "fail — lands in `conviction_log` so the gate is auditable. Setup "
        "performance is regenerated by the backtest (`stockbot backtest …`)."
    )

    st.subheader("Setup performance (walk-forward)")
    perf = all_performance()
    if not perf:
        st.info(
            "No setup performance rows yet. Run `stockbot backtest --start "
            "YYYY-MM-DD --end YYYY-MM-DD` to populate the table — every "
            "closed trade is grouped by its matched setup."
        )
    else:
        perf_df = pd.DataFrame([
            {
                "Setup": p.setup_name,
                "Direction": p.direction,
                "n trades": p.n_trades,
                "Win rate": f"{p.win_rate*100:.0f}%",
                "Avg r": f"{p.avg_r:+.3f}",
                "Expectancy": f"{p.expectancy:+.3f}",
                "Sharpe": f"{p.sharpe:+.2f}",
                "Last validated": p.last_validated_at.isoformat()[:10],
            }
            for p in perf
        ])
        st.dataframe(perf_df, use_container_width=True, hide_index=True)

    st.subheader("Recent gate evaluations")
    st.caption(
        "Newest first. Six glyphs are the per-gate verdicts: "
        "score · factor_agreement · regime · setup_validated · cooldown · "
        "data_quality. Click a row to see the verdict reasons."
    )
    rows = recent_convictions(limit=100)
    if not rows:
        st.info(
            "No conviction_log rows yet. Run `stockbot paper convict` to "
            "evaluate the current ideas."
        )
        return

    gates = ("score", "factor_agreement", "regime",
             "setup_validated", "cooldown", "data_quality")
    table_rows = []
    for r in rows:
        flags = "".join("✓" if r[f"{g}_passed"] else "✗" for g in gates)
        table_rows.append({
            "id": r["id"],
            "ts": r["ts"][:19],
            "ticker": r["ticker"],
            "side": r["instrument"],
            "score": round(r["score"], 3),
            "verdict": "PASS" if r["overall_passed"] else "FAIL",
            "gates": flags,
        })
    df = pd.DataFrame(table_rows)
    st.dataframe(df, use_container_width=True, hide_index=True)

    # Drill-down: pick a row by id, show full verdict JSON.
    row_ids = [r["id"] for r in rows]
    chosen = st.selectbox(
        "Drill into a row (full verdict reasons)",
        row_ids, format_func=lambda i: f"#{i}",
        key="conviction_drill",
    )
    drill = next(r for r in rows if r["id"] == chosen)
    verdicts = json.loads(drill["verdicts_json"])
    st.markdown(f"### #{drill['id']} · {drill['ticker']} · score {drill['score']:+.3f}")
    for gate in gates:
        v = verdicts.get(gate, {})
        marker = "✅" if v.get("passed") else "❌"
        st.markdown(f"- {marker} **{gate}** — {v.get('reason', '')}")
    if drill["pick_json"]:
        with st.expander("ConvictionPick payload"):
            st.code(json.dumps(json.loads(drill["pick_json"]), indent=2), language="json")


def _leveraged_etf_panel() -> None:
    from stockbot.strategy import leveraged_etfs as letfs

    st.markdown(
        "**For bearish views**, the system can substitute an inverse ETF for "
        "a put or a short. **For very-high-conviction bullish views**, it can "
        "suggest a leveraged long ETF. The CFO talking points: no margin, no "
        "borrow, capped at 100% loss — but **volatility drag** makes these "
        "single-day instruments, not buy-and-hold."
    )
    families = sorted({e.family for e in letfs.list_registry()})
    fam = st.selectbox("Family", ["all"] + families, index=0)
    entries = letfs.list_registry() if fam == "all" else letfs.list_registry(family=fam)
    rows = []
    for e in entries:
        rows.append({
            "Symbol": e.symbol,
            "Underlying": e.underlying,
            "Leverage": f"{e.leverage:+.0f}x",
            "Direction": "BULL" if e.leverage > 0 else "BEAR",
            "Leveraged?": "yes" if e.is_leveraged else "no",
            "Family": e.family,
            "Description": e.description,
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    st.subheader("Find alternatives for a ticker")
    c1, c2, c3 = st.columns([2, 1, 1])
    probe = c1.text_input("Ticker (single name or ETF)", value="NVDA").upper().strip()
    direction = c2.selectbox("Direction", ["bear", "bull"], index=0)
    max_lev = c3.slider("Max leverage", 1, 3, 3)
    if probe:
        alts = letfs.find_alternatives(probe, direction, max_leverage=max_lev)
        if not alts:
            st.info(f"No {direction} ETF alternatives mapped for {probe}.")
        else:
            st.caption(
                f"{probe} maps to underlying group; offering {len(alts)} "
                f"{direction} alternative(s) up to {max_lev}x:"
            )
            st.dataframe(pd.DataFrame([
                {
                    "Symbol": e.symbol,
                    "Leverage": f"{e.leverage:+.0f}x",
                    "Underlying": e.underlying,
                    "Description": e.description,
                    "Decay risk (10d)": letfs.decay_warning(e.leverage, 10) or "—",
                }
                for e in alts
            ]), use_container_width=True, hide_index=True)

    st.subheader("Why we surface the decay warning")
    st.markdown(
        "Leveraged products rebalance daily. Over a flat-but-choppy week, a 3x "
        "ETF can lose materially even if the underlying ends unchanged — the "
        "compounding works against you. This is the single biggest reason these "
        "products are inappropriate for buy-and-hold. The system flags this "
        "automatically when a recommendation would suggest holding past the "
        "configured horizon."
    )


def _portfolio_panel(cfg, portfolio: Portfolio, marks: dict[str, float]) -> None:
    equity = portfolio.equity(marks)
    cash = portfolio.cash
    target = evaluate_target(cfg, portfolio.starting_cash, portfolio.started_at, equity)
    invested = equity - cash
    cols = st.columns(5)
    cols[0].metric("Equity", f"${equity:,.0f}", f"{target.actual_return*100:+.2f}%")
    cols[1].metric("Cash", f"${cash:,.0f}")
    cols[2].metric("Invested", f"${invested:,.0f}")
    cols[3].metric(
        f"Target {target.target_annual*100:.0f}%/yr",
        f"${target.expected_equity:,.0f}",
        f"{target.delta_vs_expected:+,.0f} vs expected",
        delta_color="normal" if target.on_track else "inverse",
    )
    # Day-1 annualization is noise; suppress until we have a meaningful sample.
    if target.days_elapsed < 20:
        cols[4].metric("Annualized", "—", f"warm-up (day {target.days_elapsed}/20)")
    else:
        cols[4].metric(
            "Annualized",
            f"{target.annualized_return*100:+.2f}%",
            "ON TRACK" if target.on_track else "OFF TRACK",
            delta_color="normal" if target.on_track else "inverse",
        )


def main() -> None:
    cfg = _config()
    portfolio = _portfolio()

    st.title(":chart_with_upwards_trend: stock-bot")
    st.caption(
        "Sentiment-aware paper-trading research bot. "
        "Educational only — not investment advice."
    )

    with st.sidebar:
        st.header("Controls")
        watchlist_default = ",".join(cfg.watchlist)
        watchlist_str = st.text_area(
            "Watchlist (comma-separated tickers)",
            value=watchlist_default,
            height=120,
        )
        tickers = [t.strip().upper() for t in watchlist_str.split(",") if t.strip()]
        st.divider()
        col_run, col_scan = st.columns(2)
        do_cycle = col_run.button(":arrows_counterclockwise: Run cycle", use_container_width=True)
        do_scan = col_scan.button(":mag: Scan only", use_container_width=True)
        st.divider()
        st.caption("Danger zone")
        if st.button(":wastebasket: Reset sim", use_container_width=True, type="secondary"):
            if st.session_state.get("_confirm_reset"):
                import os
                from stockbot.config import DB_PATH
                if DB_PATH.exists():
                    os.remove(DB_PATH)
                _portfolio.clear()
                st.session_state["_confirm_reset"] = False
                st.success("Sim reset. Reloading…")
                time.sleep(0.5)
                st.rerun()
            else:
                st.session_state["_confirm_reset"] = True
                st.warning("Click again to confirm reset.")

    # Execute actions.
    cycle_result = None
    scan_ideas = None
    if do_cycle:
        with st.spinner("Running paper-trading cycle…"):
            cycle_result = engine.step(cfg, portfolio, tickers)
        if cycle_result.closed:
            st.success(f"Closed: " + " | ".join(cycle_result.closed))
        if cycle_result.opened:
            st.success(f"Opened: " + " | ".join(cycle_result.opened))
        if not cycle_result.closed and not cycle_result.opened:
            st.info("No actions this cycle.")
    if do_scan:
        with st.spinner("Scanning…"):
            scan_ideas = generate_ideas(cfg, tickers)

    # Refresh marks for the panel.
    _, marks = _open_positions_df(portfolio)
    _portfolio_panel(cfg, portfolio, marks)

    (
        tab_pos, tab_ideas, tab_curve, tab_closed,
        tab_recs, tab_risk, tab_stress, tab_score, tab_letf, tab_convict,
    ) = st.tabs(
        [
            "Open positions",
            "Scan ideas",
            "Equity curve",
            "Closed trades",
            "Recommendations",
            "Risk",
            "Stress",
            "Score breakdown",
            "Leveraged ETFs",
            "Conviction",
        ]
    )

    with tab_pos:
        df, _ = _open_positions_df(portfolio)
        if df.empty:
            st.info("No open positions. Hit *Run cycle* in the sidebar to generate trades.")
        else:
            st.dataframe(df, use_container_width=True, hide_index=True)

    with tab_ideas:
        if scan_ideas is None:
            st.caption("Hit *Scan only* to rank the current watchlist without trading.")
        elif not scan_ideas:
            st.warning("No ideas above threshold for this watchlist.")
        else:
            rows = []
            for i in scan_ideas:
                side = i.instrument.upper()
                if i.instrument != "equity":
                    strike = f"{i.option_strike:.1f}" if i.option_strike else "?"
                    exp = i.option_expiration or "?"
                    side = f"{i.instrument.upper()} {strike} {exp}"
                rows.append({
                    "Ticker": i.ticker,
                    "Side": side,
                    "Score": round(i.score, 3),
                    "Last": round(i.last_price, 2),
                    "RSI": round(i.rsi, 1),
                    "Sentiment": round(i.sentiment_net, 2),
                    "Conf.": round(i.sentiment_confidence, 2),
                    "Notes": "; ".join(i.reasons[:4]),
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    with tab_curve:
        df = _equity_curve_df(portfolio)
        if df.empty or len(df) < 2:
            st.info("Equity curve will appear after a couple of cycles.")
        else:
            st.line_chart(df.set_index("ts")["equity"])

    with tab_closed:
        df = _closed_positions_df(portfolio)
        if df.empty:
            st.info("No closed trades yet.")
        else:
            total = float(df["Realized $"].sum())
            st.metric("Realized P&L (cumulative)", f"${total:+,.2f}")
            st.dataframe(df, use_container_width=True, hide_index=True)

    with tab_recs:
        st.markdown(
            "**Every recommendation is logged — executed or not.** SKIP rows are "
            "ideas the model generated that a guardrail or sizing limit rejected. "
            "The audit policy requires this trail."
        )
        df, rate = _recommendations_df(limit=100)
        if df.empty:
            st.info("No recommendations logged yet. Run a cycle in the sidebar.")
        else:
            rc1, rc2, rc3 = st.columns(3)
            rc1.metric("Recommendations logged", rate["total"])
            rc2.metric("Trades placed", rate["executed"])
            rc3.metric(
                "Execution rate",
                f"{rate['rate']*100:.0f}%",
                f"avg score {rate['avg_score']:+.2f}",
            )
            st.dataframe(df, use_container_width=True, hide_index=True)

    with tab_risk:
        _risk_panel(portfolio)

    with tab_stress:
        _stress_panel(portfolio)

    with tab_score:
        _score_panel(cfg, tickers)

    with tab_letf:
        _leveraged_etf_panel()

    with tab_convict:
        _conviction_panel()

    st.divider()
    st.caption(
        f"Started: {portfolio.started_at[:19]} · "
        f"Starting cash: ${portfolio.starting_cash:,.0f} · "
        f"Last refresh: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC"
    )


if __name__ == "__main__":
    main()
