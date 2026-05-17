# Demo Runbook

A 10-minute live walkthrough for an executive audience. Run from the
project root with the venv active: `source .venv/bin/activate`.

## Before you start

1. `source .venv/bin/activate`
2. Optional: reset the simulation to a clean slate so the audit trail starts
   from today. `cp data/stockbot.db data/stockbot.db.bak && rm -f data/stockbot.db`
3. Run `python -m stockbot.main paper run --days 3` once so the portfolio,
   recommendation log, and journal have entries to show.

## The 5-command demo

In order. Each one answers a question you should restate out loud.

### 1. "How does it generate ideas?"

```
python -m stockbot.main scan --watchlist AAPL,MSFT,NVDA,TSLA,SPY
```

Talking points:
- Combines a six-factor cross-sectional model (value, momentum, quality,
  low-vol, size, mean-reversion) with social-sentiment scoring.
- Output is *ranked candidates*, not executions. Nothing trades from `scan`.
- The score is the model's conviction; the "Details" column is the
  human-readable reasoning.

### 2. "What did it actually do?"

```
python -m stockbot.main report
```

Talking points:
- Paper-trading only. No real capital is at risk.
- The header shows progress against the 40%/yr target. In warm-up
  (< 20 trading days) annualization is suppressed because day-1 noise
  annualizes to nonsense and we don't want to mislead.
- Each position shows entry, current mark, P&L, and the pre-computed
  stop and target.

### 3. "Where's the audit trail?"

```
python -m stockbot.main rec-log
```

Talking points:
- **Every recommendation is logged, executed or not.** SKIP ≠ ignored — it
  means the recommendation was generated, persisted with its full thesis,
  and rejected by a guardrail or sizing limit.
- Execution rate is intentionally below 100% — that's the discipline.
- Each recommendation row carries the config hash so any past suggestion
  is exactly reproducible.

### 4. "How much could we lose tomorrow?"

```
python -m stockbot.main risk-report
```

Talking points:
- 1-day VaR/CVaR at 95% and 99%, both parametric (Gaussian assumption)
  and historical (empirical tail).
- Portfolio Greeks — Δ (directional exposure), Γ (convexity), Θ (time
  decay/day), ν (vega per 1 vol pt), ρ (rates).
- These are the same risk metrics a real desk would publish.

### 5. "How would this have done in 2008 / COVID / 2022?"

```
python -m stockbot.main stress
```

Talking points:
- Replays the *current* portfolio composition through five historical
  regimes. The drawdowns are honest — this is what could happen.
- 2022 rates shock is typically the worst because the book skews growth.

### Bonus: "Has the strategy actually worked historically?"

```
python -m stockbot.main backtest --start 2024-01-01 --end 2025-01-01
```

**Be honest about the number.** A backtest is not a forecast.
- This is a single one-year window, not walk-forward.
- The factor weights in `config.yaml` were chosen by hand; they are not
  blind-tested.
- The framework computes a *deflated* Sharpe (Bailey & López de Prado)
  that corrects for multiple-testing — that's what to quote, not the
  raw Sharpe.

## Q&A — answers prepared

**"Why aren't we trading real money?"**
By design. The `Broker` interface exists; a real implementation (IBKR,
Tradier, Alpaca) is intentionally not wired. Going live requires explicit
per-order approval (`auto_approve=False` is the default) and a
multi-step override for any policy breach. See `docs/NON_GOALS.md` §3.

**"What happens if it loses a lot in one day?"**
Loss cooldown triggers automatically (`ops/guardrails.py`). New positions
are blocked for the configured cooldown window. Overrides are
audit-logged.

**"Why should I trust the backtest?"**
You shouldn't, fully. That's why we have deflated Sharpe, walk-forward
support, point-in-time data slicing, a realistic cost model (commissions,
half-spread bps, square-root impact), and historical stress replay. The
backtest is a sanity check, not a forecast. See `docs/NON_GOALS.md` §4.

**"What data are you using?"**
Today: `yfinance` (free). The data layer is vendor-agnostic — Polygon,
AlphaVantage, and IEX adapters are stubbed and activate once API keys
are added. Sentiment from Reddit and StockTwits (Reddit needs creds).

**"What can it not do?"**
Read `docs/NON_GOALS.md` aloud if asked. The short version: no claimed
predictive accuracy, no execution-speed advantage, no auto-trading
without human approval, no insider data, no investment advice.

## If a command errors during the demo

- `Reddit credentials not set; skipping Reddit scan.` — Expected. Sentiment
  falls back to StockTwits only.
- Empty position table after a reset — Run `paper run --days 3` first.
- yfinance rate-limit hiccups — Re-run the command; the cache will catch.
