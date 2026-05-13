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
| `backtest` | (stub) Replay historical data against the strategy. |

## Project layout

```
stockbot/
  data/         # market data (yfinance) and options chains
  sentiment/    # Reddit + StockTwits scanners and aggregator
  strategy/     # technical indicators, scoring, option selection
  portfolio/    # virtual portfolio, risk sizing, 40% target tracker
  engine/       # paper trading loop
  reporting/    # CLI report rendering
```

State lives in `data/stockbot.db` (SQLite). Delete it to reset the sim.

## Configuration

`config.yaml` controls watchlist, risk parameters, sentiment weights, and target
return. `.env` holds API credentials (Reddit only, for the free tier).

## Status

Initial scaffold — paper loop and CLI work end-to-end on free data sources.
Backtester, web dashboard, and X/Twitter integration are stubbed for follow-up.
