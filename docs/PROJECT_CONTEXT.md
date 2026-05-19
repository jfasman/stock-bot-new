# stock-bot — project context

A self-contained brief on what this project is, how it's organized, what's
built, and what's intentionally not built. Paste this into a fresh Claude
conversation when you want help shaping new requirements — it should give
the model enough context to give grounded advice without re-discovering
the codebase.

---

## 1. What it is

A **paper-trading research bot** that scores a watchlist of equities and
proposes swing-trade ideas — long equity, long calls, long puts, and
inverse / leveraged ETFs as alternatives to shorting. It runs entirely in
simulation against a virtual portfolio and persists state to a local
SQLite database (`data/stockbot.db`).

**It is not** an investment advisor, an auto-trader, or a live execution
platform. The `Broker` interface exists; a real broker implementation
(IBKR/Tradier/Alpaca) is intentionally not wired. Going live would
require an explicit opt-in and a per-order approval flow.

**Audience.** A single disciplined retail user (the project owner) who
wants a system that surfaces ideas with a documented thesis, applies risk
controls automatically, and produces an honest audit trail — including
the recommendations it *rejected*.

**Stated target.** A 40%/yr annualized return, treated as
ambitious-but-feasible. The framework tracks performance against that
target and surfaces drift, but the system's discipline (sizing, stops,
stress tests, guardrails) is the actual product — the target number is a
benchmark, not a promise.

---

## 2. How a single cycle works

```
                 watchlist (config.yaml or CLI flag)
                            │
                            ▼
        ┌────────────────────────────────────────────┐
        │ stockbot.data.prices.get_history()         │  vendor-pluggable
        │  + stockbot.sentiment.aggregator.aggregate │  (yfinance default)
        └────────────────────────────────────────────┘
                            │
                            ▼
        ┌────────────────────────────────────────────┐
        │ stockbot.strategy.scorer.read_technicals   │  trend / momentum /
        │ stockbot.strategy.scorer.composite_score   │  breakout, blended
        │                                            │  with sentiment
        └────────────────────────────────────────────┘
                            │
                            ▼
        ┌────────────────────────────────────────────┐
        │ stockbot.strategy.ideas.generate_ideas()   │  produces Idea[]:
        │  → equity / call / put / inverse-ETF /     │  long / short across
        │    leveraged-long-ETF (when conviction is  │  multiple instruments
        │    high enough)                            │
        └────────────────────────────────────────────┘
                            │
                            ▼
        ┌────────────────────────────────────────────┐
        │ stockbot.engine.paper.step():              │
        │  for each idea:                            │
        │   1. size (Kelly / vol-target via sizing)  │
        │   2. build Thesis with stop, target,       │
        │      invalidation, config hash             │
        │   3. log thesis to recommendations table   │  ← every rec logged,
        │      (executed OR not)                     │    executed or not
        │   4. run guardrails (concentration, loss   │
        │      cooldown, leverage)                   │
        │   5. if allowed → open position, mark      │
        │      thesis EXECUTED, journal entry,       │
        │      audit_log entry                       │
        └────────────────────────────────────────────┘
```

Every cycle also snapshots the config (`ops/config_snapshot.py`) so that
*yesterday's* recommendation can be exactly reproduced from
`{thesis, config_hash}`.

---

## 3. Module map

```
stockbot/
  config.py                   single Config dataclass, YAML-loaded
  main.py                     Click CLI: scan, paper, report, rec-log,
                              risk-report, stress, backtest, explain

  data/
    prices.py                 thin wrapper that routes to a vendor
    fundamentals.py           Fundamentals dataclass + 6h cache
    macro.py                  VIX / yield-curve / DXY snapshot
    corporate_actions.py      split-adjust + delisting filter
    greeks.py                 Black-Scholes pricing, greeks, IV solver
    options.py                option-chain helpers
    universe.py               watchlist resolution
    vendors/
      base.py                 abstract DataVendor interface
      registry.py             get_vendor() — env-driven selection
      yfinance_vendor.py      default impl (free, current-snapshot)
      polygon_stub.py
      alphavantage_stub.py    paid vendor stubs — wired by env var,
      iex_stub.py             require API keys to activate

  sentiment/
    aggregator.py             AggregateSentiment (net, confidence)
    reddit.py                 PRAW client (needs creds)
    stocktwits.py             public endpoint
    nlp.py                    rule-based scorer (no model dependency)

  strategy/
    indicators.py             SMA, RSI, MACD, Bollinger
    scorer.py                 TechnicalRead + composite_score
                              (50% trend + 30% momentum + 20% breakout,
                               then blended with sentiment by weight)
    ideas.py                  generate_ideas → Idea[] with equity / call /
                              put / etf branches
    options_signals.py        IV rank/percentile (proxy), put/call skew,
                              vertical / iron-condor / covered-call /
                              CSP builders
    thesis.py                 Thesis dataclass — falsifiable hypothesis,
                              invalidation triggers, expected return band,
                              config hash for reproducibility
    score_explain.py          ScoreBreakdown + explain_score() — exposes
                              every sub-signal, weight, contribution, and
                              the exact arithmetic that produced the score
    leveraged_etfs.py         32-symbol registry of leveraged / inverse
                              ETFs across broad / tech / semi / financial /
                              energy / gold, with sector-proxy mapping
                              (NVDA→SOXX, AAPL→QQQ, JPM→XLF, …) and
                              volatility-drag warnings
    cross_section.py          zscore, sector-neutralize, rank_pct
    factors/                  value, momentum, quality, lowvol, size,
                              meanrev → composite (parallel to scorer;
                              displayed in score_explain but not currently
                              fused into the live score)

  portfolio/
    portfolio.py              Portfolio + Position with multiplier-aware
                              cost basis (options = 100x, equity & etf 1x)
    store.py                  SQLite schema (positions, trades, portfolio,
                              equity_curve, recommendations, audit_log,
                              config_snapshots, trade_journal,
                              guardrail_state)
    sizing.py                 fractional Kelly, vol-target, risk parity
    var.py                    parametric VaR/CVaR (Gaussian) +
                              historical VaR/CVaR (empirical tail)
    stress.py                 historical regime replay (gfc_2008,
                              covid_crash_2020, rates_shock_2022,
                              dotcom_2000, volmageddon_2018)
    greeks_agg.py             portfolio Δ/Γ/Θ/ν/ρ — ETF delta scales by
                              signed leverage factor
    constraints.py            sector/exposure/beta limits, correlation
                              warning
    risk.py                   stops/targets (legacy, simpler view)
    targets.py                target-return tracker with warm-up
                              suppression (< 20 days = no annualization)

  engine/
    broker.py                 abstract Broker, default auto_approve=False
    paper_broker.py           in-memory paper Broker
    paper.py                  the cycle loop (wired with guardrails,
                              thesis logging, audit, journal, cooldown)
    orders.py                 Quote, limit_from_book (passive→aggressive),
                              build_equity_order, build_option_order

  backtest/
    engine.py                 event-driven daily backtester with
                              point-in-time _slice_as_of() and a corrected
                              cash-accounting model (post-bug-fix)
    costs.py                  commissions, half-spread bps, sqrt impact,
                              FINRA fees, option spreads
    metrics.py                Sharpe / Sortino / drawdown / win rate /
                              profit factor + deflated_sharpe
                              (Bailey & López de Prado)
    walkforward.py            train / test windows for out-of-sample
                              validation

  ops/
    recommendation_log.py     log_thesis (every rec, executed or not),
                              mark_executed, hit_rate (= execution rate)
    audit.py                  log_event / recent_events
    config_snapshot.py        hash_config / snapshot / get_snapshot
    guardrails.py             trigger_loss_cooldown, check_pre_trade,
                              record_override, deviation_prompt
    wash_sale.py              IRS 30-day rule detection
    pdt.py                    FINRA pattern-day-trader rule check

  reporting/
    report.py                 rich-rendered CLI tables (scan, portfolio)
    attribution.py            OLS factor attribution + PnL decomposition
    drift.py                  vol_drift / performance_drift
    journal.py                trade-journal record/lookup

dashboard.py                  Streamlit UI — 9 tabs:
                                Open positions, Scan ideas, Equity curve,
                                Closed trades, Recommendations (audit),
                                Risk (VaR + Greeks), Stress (regime
                                replay), Score breakdown (per-ticker
                                decomposition), Leveraged ETFs (registry
                                + alternative finder)

docs/
  NON_GOALS.md                8 explicit non-goals
  DEMO_RUNBOOK.md             5-command CFO demo walkthrough
  PROJECT_CONTEXT.md          this file

tests/                        ~83 tests covering greeks, factors, sizing,
                              VaR, constraints, wash-sale, PDT, backtest
                              (incl. cash-leak regression), thesis,
                              orders, leveraged_etfs, score_explain
```

---

## 4. CLI surface

```
python -m stockbot.main --help

  scan           Score the watchlist; print ranked ideas
  paper run      Step the cycle forward N days
  paper open     Manually open a position
  paper close    Manually close a position
  report         Portfolio status + open positions + target tracking
  rec-log        Recent recommendations (executed OR skipped)
  risk-report    1-day VaR/CVaR + portfolio Greeks
  stress         Replay current book through historical regimes
  backtest       Walk-forward-ready backtest over the watchlist
  explain TICKER Full breakdown of a ticker's composite score
```

The Streamlit dashboard mirrors all of these and runs at
`http://localhost:8501` (`streamlit run dashboard.py`).

---

## 5. Scoring — how a number becomes an idea

There are **two parallel scoring systems** and this is a frequent source
of confusion when discussing requirements:

### a) The live "dashboard score" (used to make trade decisions)
Lives in `strategy/scorer.py`. Pipeline:
1. **TechnicalRead** from `read_technicals(df, cfg)`:
   - `trend` ∈ [−1, +1]: relationship of price to SMA20/SMA50 + slope
   - `momentum` ∈ [−1, +1]: (RSI − 50) / 30 + sign(MACD histogram)
   - `breakout` ∈ [−1, +1]: position within Bollinger band, clamped
2. **tech_composite** = 0.50 × trend + 0.30 × momentum + 0.20 × breakout
3. **sentiment_subscore** = sentiment.net × sentiment.confidence (or 0 if
   confidence is 0 — i.e. when Reddit creds are missing)
4. **final** = (1 − sw) × tech_composite + sw × sentiment_subscore,
   where `sw` defaults to 0.4
5. Final is clipped to [−1, +1]. Long threshold +0.55, short threshold
   −0.55, both configurable.

`strategy/score_explain.py:explain_score()` returns a `ScoreBreakdown`
preserving every intermediate value — used by the CLI `explain` command
and the dashboard "Score breakdown" tab.

### b) The factor model (parallel cross-sectional signal)
Lives in `strategy/factors/`. Six classical factors — value, momentum,
quality, lowvol, size, meanrev — each implemented per the textbook
definition, sector-neutralized when there are enough names, combined via
configurable weights into a cross-sectional composite. **It is displayed
in the score-explanation view but not currently fused into the live
score.** The backtester uses it directly to score the universe each
rebalance.

If you want to propose changes to scoring, be explicit about which of the
two you mean.

---

## 6. Instruments supported

| instrument | direction          | how it expresses bearish view              | leverage              |
| ---------- | ------------------ | ------------------------------------------ | --------------------- |
| `equity`   | `long` / `short`   | short (not currently emitted by ideas.py)  | 1x                    |
| `call`     | `long` only        | n/a (bullish)                              | 100x contract         |
| `put`      | `long` only        | the default bear expression                | 100x contract         |
| `etf`      | `long` only        | inverse ETF (e.g. SH −1x, SQQQ −3x)        | signed factor in registry |

For a bearish signal, `ideas.py` currently emits **both** a put idea and
an inverse-ETF idea (1x by default, leveraged inverse at high
conviction). For very-high-conviction bullish (score ≥ 0.85), it adds a
leveraged long ETF idea (e.g. TQQQ for QQQ-mapped names).

---

## 7. Risk & operational controls

- **Position sizing**: `portfolio/sizing.py` offers fractional Kelly,
  vol-target, and risk-parity. The paper engine uses `size_equity` and
  `size_option` with stop/target derived from `risk.stop_loss_pct` and
  `risk.take_profit_pct`. ETF positions divide quantity by leverage so
  notional exposure stays inside the per-position cap.
- **Guardrails** (`ops/guardrails.py`): pre-trade check for
  concentration, leverage, and loss-cooldown; multi-step override
  required to breach, audit-logged.
- **VaR / CVaR**: 1-day parametric (Gaussian) and historical (empirical
  tail) at 95% and 99%, dollar-denominated.
- **Stress test**: replays *current* portfolio weights through five
  historical regimes; not a forecast, a tail sanity-check.
- **Wash-sale** (`ops/wash_sale.py`) and **PDT** (`ops/pdt.py`)
  detection live as utilities; reporting is opt-in via config.
- **Audit trail**: `recommendations`, `audit_log`, `config_snapshots`,
  `trade_journal`, `guardrail_state` are all in the SQLite DB. Every
  rec is logged, every override is logged, every config change is
  hashed.

---

## 8. Configuration (`config.yaml`)

Top-level sections worth knowing about:

- `portfolio` — starting cash, target return, max position pct, max open positions, cash reserve.
- `risk` — equity stops / targets, option stops / targets, max daily drawdown.
- `strategy` — RSI levels, min_score thresholds, sentiment weight, swing lookback.
- `options` — DTE preferences, delta targets, max premium pct.
- `sentiment` — Reddit subs, post limits, StockTwits limits, cache.
- `data` — active vendor (yfinance | polygon | alphavantage | iex).
- `factors` — per-factor weights and `sector_neutral` toggle.
- `backtest` — starting cash, rebalance days, max position weight.
- `risk_advanced` — VaR lookback, vega/gamma thresholds, sector and exposure limits, beta range, correlation warning.
- `guardrails` — cooldown hours, concentration cap, leverage cap, override step count.
- `leveraged_etfs` — enabled flag, max leverage factor, prefer-inverse-over-short, high-conviction threshold for leveraged longs, gross-leverage cap.
- `compliance` — toggles for PDT check and wash-sale tracking.
- `watchlist` — default list of tickers if not overridden on CLI.

---

## 9. Data layer — current capabilities and gaps

**Active**: yfinance, free, current-snapshot for fundamentals (not
point-in-time), reasonable historical OHLCV. Sufficient for the
backtester and for technicals; **not sufficient** for production-grade
factor research.

**Stubs awaiting API keys**: Polygon, AlphaVantage, IEX. The
`DataVendor` interface guarantees that adding a paid vendor only
requires implementing `history / last_price / fundamentals /
corporate_actions / is_delisted / supports_point_in_time` and setting
`STOCKBOT_VENDOR` env var or `data.vendor` in config.

**Sentiment**: Reddit requires creds in `.env`. StockTwits public
endpoint is used unauthenticated. X/Twitter is not wired.

**Macro**: VIX, yield curve, DXY snapshot via yfinance proxies. FRED
hookup exists (`fred_series()`) but requires `FRED_API_KEY`.

**Known accuracy caveats**:
- yfinance fundamentals are current-snapshot, not point-in-time — the
  backtester technically reads "today's" fundamentals at every step.
  Acceptable for demos, not for serious factor research.
- IV rank in `options_signals.py` uses a realized-volatility proxy
  because we don't have a real options-chain history without a paid
  vendor.
- Sentiment is a simple rule-based score, not an NLP model.

---

## 10. Non-goals — what the bot will not do

Verbatim from `docs/NON_GOALS.md` (read that file for the longer version):

1. **Predict short-term price moves with claimed high accuracy.**
2. **Compete on execution speed.** Retail infrastructure cannot beat HFT.
3. **Auto-trade real money without human approval.** Even when a real
   broker is wired, default is `auto_approve=False`.
4. **Treat backtested Sharpe as expected live Sharpe.** Walk-forward,
   deflated Sharpe, and stress tests exist precisely because in-sample
   performance lies.
5. **Recommend option structures whose risk the user cannot articulate.**
6. **Trade on insider, non-public, or unauthorized information.**
7. **Provide investment advice.** Output is research, not advice.
8. **Mask losses.** Every rec, override, and config change is logged.

---

## 11. Current status & open seams

What is **production-ready for a paper demo**:
- The full Phase 1 + Phase 2 roadmap (roadmap-section.md §13.1–§13.8)
  has shipped to `main`.
- 355/355 tests pass, including the regression test for the
  previously-found backtest cash-leak bug.
- The Streamlit dashboard has five top-level tabs (Today / Portfolio /
  Ideas / Conviction / Live read-only), with a Rebalance sub-tab under
  Portfolio and a sub-grouped audit panel under Conviction.

What was on the "known gaps" list as of mid-2026 and has now **shipped**:
- ~~No real broker.~~ → **Shipped, read-only.** `engine/brokers/`
  has Alpaca / Tradier / IBKR Client Portal Gateway impls behind a
  `LiveBroker` Protocol that *cannot* place orders (the read-only
  guarantee is enforced by type, not a flag). The Phase B per-order
  approval router is scaffolded (the `pending_orders` table + the
  `ops/live_order_audit.py` state machine) but not yet built —
  that's a future cluster. See roadmap §13.7.
- ~~Factor model not fused.~~ → **Shipped.** `composite_score` now
  takes a `factor_composite` argument and the live cycle's
  `generate_ideas` computes a cross-sectional composite against
  `factors.peer_pool`. Renormalizes to the two-way blend when the
  universe is below `factors.min_universe`. `backtest-fusion` CLI
  runs the unfused-vs-fused side-by-side on the same window. See
  roadmap §13.6.
- ~~No portfolio optimizer.~~ → **Shipped.** `portfolio/rebalancer.py`
  has three pure algos — equal_risk_contribution, vol_target_book,
  mean_variance_with_views — wired into a propose/approve/execute
  workflow with audit (`rebalance_proposals` table). Grows clear the
  conviction gate at current prices; shrinks skip it. `paper
  rebalance(-approve/-reject/-execute/-log)` CLI and dashboard
  Rebalance sub-tab. See roadmap §13.8.
- ~~No real-time loop.~~ → **Shipped.** `paper watch` runs the
  conviction gate + notification dispatch on a NYSE-hours cadence.
  See roadmap §13.3.
- ~~No alerting.~~ → **Shipped.** `ops/notify.py` defines a
  `Notifier` Protocol with stdout / file / Pushover backends,
  rate-limited by `notifications.max_per_day` and
  `max_per_ticker_per_week`, with ack/snooze via CLI and dashboard.
  See roadmap §13.3.

Still on the gap list:
- **Sentiment is rule-based.** A small transformer would lift quality
  but pulls in a dependency.
- **Phase B live execution.** The read-only Phase A is in. Phase B
  ships a `LiveOrderRouter` that drives the existing `pending_orders`
  state machine into real `submit_order` calls. Out of scope for the
  Phase 2 cluster that shipped Phase A.
- **Partial-grow / partial-shrink in `execute_rebalance`.** First-cut
  executor handles full-open and full-close only; partials log a
  "not yet supported" note and the proposal still flips to
  `executed`. The audit captures every decision so a follow-up can
  add partial sizing without losing the trail.
- **Point-in-time fundamentals.** The vendor returns latest; both legs
  of `compare_fusion` share the look-ahead, so the *delta* is
  informative but the absolute numbers are inflated. A real PIT
  fundamentals feed is its own project.

---

## 12. How to use this document with Claude chat

When you start a new conversation with Claude (web or API) to scope a
new requirement, paste this file at the top of the conversation and
then write your request — for example:

> "Here's the project context for stock-bot. I want to add a feature
> that does X. What's the simplest way to do this given the existing
> module structure?"

The chat will then know:
- The architecture (so it points at the right modules)
- The non-goals (so it won't suggest auto-trading)
- The known gaps (so it won't suggest things that already exist)
- The discipline norms (audit trail, falsifiable thesis, point-in-time
  data) so its suggestions stay consistent with the project's values

If the requirement involves a financial term or instrument not yet in
the system, also link the chat to `docs/NON_GOALS.md` so it doesn't
propose something the project has explicitly ruled out.

Last updated by the implementing assistant based on the code state at
the time this file was written. Refresh if the module map or status
drift more than ~20%.
