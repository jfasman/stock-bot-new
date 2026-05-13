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

    tab_pos, tab_ideas, tab_curve, tab_closed = st.tabs(
        ["Open positions", "Scan ideas", "Equity curve", "Closed trades"]
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

    st.divider()
    st.caption(
        f"Started: {portfolio.started_at[:19]} · "
        f"Starting cash: ${portfolio.starting_cash:,.0f} · "
        f"Last refresh: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC"
    )


if __name__ == "__main__":
    main()
