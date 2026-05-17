# stock-bot

A paper-trading research bot that combines technical signals with social-sentiment
scanning (Reddit, StockTwits) to generate **swing-trade** and **options (calls/puts)**
ideas. It runs entirely in simulation against a virtual portfolio so you can track
performance without risking capital.

> Educational / research use only. This is not investment advice. Past performance
> and simulated performance do not guarantee future results.

## Goals

- **Target return:** ~40% annualized — chosen as ambitious-but-feasible. Risk
  controls (per-trade sizing, stop-loss, max drawdown) are the primary tools to
  defend it. The bot will *flag* when its trailing return is materially off track.
- **Sentiment-aware picks:** every candidate is scored on price action *and*
  aggregated chatter from Reddit (r/wallstreetbets, r/stocks, r/options,
  r/investing) and StockTwits. Optional X/Twitter via API key.
- **Bull or bear:** generates long-equity, long-call, and long-put ideas, plus
  defined-risk verticals when conviction is mixed.
- **Swing-first:** default horizon is 3–20 trading days. Configurable.

## Quick start

Requires **Python 3.10+** (tested on 3.13). If you don't have a modern Python,
[`uv`](https://docs.astral.sh/uv/) is the easiest way to install one without
Homebrew: `curl -LsSf https://astral.sh/uv/install.sh | sh && uv python install 3.13`.

```bash
uv venv --python 3.13 .venv     # or: python3 -m venv .venv
source .venv/bin/activate
uv pip install -r requirements.txt   # or: pip install -r requirements.txt
cp .env.example .env   # add Reddit credentials if you want live sentiment
python -m stockbot.main scan --watchlist AAPL,MSFT,NVDA,TSLA,SPY
python -m stockbot.main paper run --days 30
python -m stockbot.main report
```

Sub-commands:

| Command | What it does |
| --- | --- |
| `scan` | Score the watchlist (TA + sentiment) and print ranked ideas. |
| `paper run` | Step the paper-trading loop forward N trading days. |
| `paper open / close` | Manually open/close a simulated position. |
| `report` | P&L, open positions, target tracking, trade log. |
| `risk-report` | Portfolio VaR/CVaR (parametric + historical) and aggregate Greeks. |
| `stress` | Replay current positions through historical regimes (2008, COVID, 2022). |
| `rec-log` | Show recent recommendations (executed and skipped) and hit rate. |
| `backtest` | Walk-forward-ready backtester using the factor model. |

## Project layout

```
stockbot/
  data/
    vendors/      # pluggable vendor adapters (yfinance default; polygon, alphavantage, iex stubs)
    fundamentals.py    macro.py    corporate_actions.py    greeks.py
  sentiment/      # Reddit + StockTwits scanners and aggregator
  strategy/
    indicators.py  scorer.py  ideas.py   thesis.py     options_signals.py
    cross_section.py
    factors/      # value / momentum / quality / lowvol / size / meanrev / composite
  portfolio/
    portfolio.py  risk.py  targets.py  store.py
    sizing.py     # fractional Kelly / vol-target / risk parity
    var.py        # parametric + historical VaR/CVaR
    stress.py     # historical regime replay
    greeks_agg.py # portfolio-level Greeks
    constraints.py
  engine/
    paper.py      # paper-trading loop (wired with guardrails + recommendation log)
    broker.py     # abstract Broker interface
    paper_broker.py  orders.py
  backtest/       # event-driven engine, costs, walk-forward, metrics (deflated Sharpe)
  ops/            # guardrails, audit log, recommendation log, wash-sale, PDT, config snapshots
  reporting/      # CLI rendering, factor attribution, drift detection, trade journal
docs/
  NON_GOALS.md    # explicit non-goals
```

State lives in `data/stockbot.db` (SQLite). Delete it to reset the sim.

## Configuration

`config.yaml` controls watchlist, risk, factor weights, backtest parameters,
guardrail thresholds, and compliance toggles. `.env` holds API credentials
(Reddit for sentiment; `POLYGON_API_KEY` / `ALPHAVANTAGE_API_KEY` / `IEX_API_KEY` /
`FRED_API_KEY` are read when present).

## Discipline notes

- **Every recommendation is logged**, executed or not (`ops/recommendation_log.py`).
- **Every config change is hashed and snapshotted** (`ops/config_snapshot.py`) so
  yesterday's recommendation is always reproducible.
- **Guardrails** (`ops/guardrails.py`) enforce loss-cooldown, concentration, and
  leverage limits — breaches require multi-step override and are audit-logged.
- **Backtests** use point-in-time data slices (`backtest/engine.py`) and a realistic
  cost model (`backtest/costs.py`). Treat the resulting Sharpe with skepticism —
  see `backtest/metrics.py:deflated_sharpe` for multiple-testing correction.
- See [`docs/NON_GOALS.md`](docs/NON_GOALS.md) for what this bot will not do.

## Status

Foundations in place across all 10 sections of the requirements spec. Paid
vendor adapters (Polygon, AlphaVantage, IEX) are stubs awaiting API keys.
Real-broker (IBKR, Tradier, Alpaca) implementations of the `Broker` interface
are intentionally not included — that step requires explicit user opt-in.
