## 13. Roadmap — conviction-gated alerting

The project's edge is not "more ideas." It is **fewer, filtered, audited
ideas, delivered when they matter.** Everything in §1–§12 is in service
of that thesis but stops one step short: `generate_ideas()` produces a
ranked list, and the user has to go look at it. The roadmap below closes
that gap without violating any non-goal in §10 — in particular, **no
auto-execution, no investment advice, no claim of predictive accuracy.**
The alert is a research artifact, not a trade signal.

The work is split into three clusters, intended to land in order. Each
cluster is independently shippable and independently testable.

### 13.1 Cluster 1 — the conviction gate

**Goal.** A second, stricter layer between "idea generated" and "user
alerted." Today, `strategy/ideas.generate_ideas()` emits anything
clearing `strategy.min_score`. That's the right bar for *displaying* an
idea in the scan output. It is *not* the right bar for interrupting the
user's day. The conviction gate is the right bar.

**Module.** `strategy/conviction.py`, paralleling `strategy/scorer.py`
in spirit — pure functions over data, no side effects, fully unit
tested.

**Shape.** A `ConvictionPick` dataclass = `Idea` + the gates it cleared
+ a confidence band + a `time_to_act` enum (`open_tomorrow`,
`this_week`, `opportunistic`). The gate is the conjunction of
independent checks; each check returns `(passed: bool, reason: str)` so
that failures are diagnosable.

**Gates (all must pass).**

1. **Score gate.** Composite score (per §5a) ≥ `conviction.notify_threshold`,
   strictly greater than `strategy.min_score`. Default 0.70 vs 0.55.
2. **Factor agreement gate.** The factor composite from
   `strategy/factors/` must agree on direction with the live score.
   Disagreement → reject. This is the first time the factor model
   influences the live pipeline; it solves the "factor model not fused"
   gap from §11 without forcing a full rewrite of the scorer.
3. **Regime gate.** Macro snapshot (VIX, yield-curve slope, DXY trend)
   must be inside configured bounds for the *setup type* being
   proposed. A momentum-long is rejected when VIX > `regime.vix_max_long`;
   a mean-reversion long is allowed in higher vol. Bounds live in
   `config.yaml` under `conviction.regime`.
4. **Setup-validated gate.** The idea must match a named setup from
   the library (§13.2), and that setup must have positive expectancy in
   the walk-forward log within `conviction.setup_validation_window_days`.
   Setups with insufficient sample size (`n < conviction.min_setup_trades`)
   fail closed — never alert on an unvalidated setup.
5. **Cooldown gate.** No notification has fired for this ticker within
   `conviction.cooldown_hours`. Prevents oscillation around the threshold.
6. **Data-quality gate.** No stale prices, no recent corporate action
   unaccounted for, fundamentals cache fresh. Cheap to check, prevents
   embarrassing alerts on bad data.

**Logging.** Every evaluation — pass *or* fail — is written to a new
`conviction_log` table with the gate-by-gate verdict. This is the
analogue of `recommendations` for the alert layer: the audit trail
includes the picks we *did not* surface and why. Critical for tuning
the gates later without flying blind.

**Config.** New top-level `conviction:` section in `config.yaml`:

```yaml
conviction:
  notify_threshold: 0.70
  cooldown_hours: 24
  setup_validation_window_days: 180
  min_setup_trades: 20
  regime:
    vix_max_long: 28
    vix_max_short: 45
    yield_curve_inverted_blocks: [breakout_long]
```

**Tests.** A pick is constructed from fixture data; each gate is
toggled independently; verdicts are asserted. The bar is
"`conviction.evaluate(idea, context)` is deterministic given its
inputs," same standard as `scorer.composite_score`.

**Out of scope for Cluster 1.** Notifications themselves; the setup
library beyond a single hard-coded placeholder; any change to
`generate_ideas`. The gate runs *after* `generate_ideas` and is purely
additive.

### 13.2 Cluster 2 — the setup library

**Goal.** Make "filtered by our validated models" a true statement, not
a marketing line. Today the scorer is one number; the question "*why*
this pick?" has only a numeric answer. The setup library gives it a
**named, archived, measured** answer.

**Module.** `strategy/setups/` — directory paralleling
`strategy/factors/`. One file per setup. Each setup is a class:

```python
class Setup(Protocol):
    name: str
    direction: Literal["long", "short"]
    instrument_hint: Literal["equity", "call", "put", "etf"]

    def matches(self, tech: TechnicalRead,
                fundamentals: Fundamentals | None,
                options: OptionsContext | None,
                macro: MacroSnapshot) -> bool: ...

    def expected_holding_days(self) -> tuple[int, int]: ...
```

**Initial setups (deliberately small set).**

- `breakout_with_momentum` — close > 20d high, RSI > 60, MACD
  histogram positive, volume ≥ 1.5× 20d avg.
- `pullback_in_uptrend` — SMA20 > SMA50, price within 0.5 ATR of SMA20
  from above, RSI between 40 and 55.
- `mean_reversion_oversold` — RSI < 30, price ≥ 1 ATR below SMA20, no
  earnings within `setups.earnings_buffer_days`.
- `iv_crush_premium_sell` — IV rank > 70, no catalyst within DTE,
  defined-risk structure only (iron condor / vertical credit).

Four setups is enough to validate the architecture. Adding setups is a
one-file change; deliberately resisting the urge to seed twenty.

**Validation.** Extend `backtest/engine.py` to record, for each closed
trade, which setup matched at entry. The walk-forward output then
yields a `setup_performance` table:

```
setup_name | n_trades | win_rate | avg_R | expectancy | sharpe | last_validated_at
```

This table is the artifact the conviction gate's setup-validated gate
queries. It is regenerated on every `backtest` run; a setup with stale
validation (older than `conviction.setup_validation_max_age_days`)
fails closed.

**Connection to `score_explain`.** `explain_score()` gains a "matched
setups" field. The dashboard's Score Breakdown tab already exposes
sub-signals; it gains a "Setup Match" row listing every setup the
ticker currently matches and that setup's last validated expectancy.
This is the user-facing answer to "why is this pick credible?"

**Out of scope for Cluster 2.** Auto-discovery of new setups, ML-based
setup classification, anything that smells like overfitting to the
recent backtest window. Setups are hand-defined, hypothesis-driven,
and added one at a time with a written rationale committed alongside
the code.

### 13.3 Cluster 3 — the notification layer

**Goal.** The smallest reliable mechanism that turns a `ConvictionPick`
into a ping on a phone, with an audit trail and a snooze. Per §11 this
is currently an explicit gap.

**Modules.**

- `ops/notify.py` — `Notifier` Protocol plus implementations:
  `StdoutNotifier`, `FileNotifier`, `EmailNotifier`, `PushoverNotifier`,
  `SlackWebhookNotifier`. Selection via `notifications.backend` in
  config; multiple backends allowed (e.g. file + Pushover).
- `ops/scheduler.py` — a `run_watch_loop(cfg)` that calls
  `engine.paper.step()` on cadence (default every 15 min during market
  hours, configurable), evaluates the conviction gate on each idea,
  and dispatches notifications. Implementation is a plain
  `while True: sleep` loop; cron remains an option for users who
  prefer it.
- New CLI command: `paper watch` — runs the loop in the foreground
  with structured logging. `paper watch --once` runs a single pass and
  exits, useful for cron and for tests.

**Payload.** A notification contains: ticker, direction, instrument
(equity / call / put / etf), entry zone, stop, target, matched setup
name, setup expectancy, time-to-act, and a deep link to the dashboard's
Score Breakdown tab for that ticker. Bounded length so it fits in a
push notification preview.

**Persistence.** New `notifications` table: `id, ts, ticker,
conviction_pick_json, backend, delivered_ok, acked_at, snoozed_until`.
Snooze and ack are exposed as dashboard buttons and as CLI subcommands
(`paper ack <id>`, `paper snooze <id> --hours N`). A pick that is
snoozed does not re-fire until the snooze expires *or* the underlying
score moves by more than `notifications.resurface_score_delta`.

**Rate limiting.** Hard caps in config:
`notifications.max_per_day`, `notifications.max_per_ticker_per_week`.
Hitting a cap is itself an `audit_log` event, not a silent drop.

**Read-only contract.** The notification layer never calls
`Broker.submit_order`, never mutates positions, never writes to
`trade_journal`. It is downstream of `conviction.evaluate` and
upstream of the human eyeball — nothing else. This preserves
non-goal #3 ("auto-trade real money without human approval") even
once a live broker is wired.

**Config.**

```yaml
notifications:
  backend: [file, pushover]   # list, all dispatched
  pushover:
    user_key_env: PUSHOVER_USER_KEY
    api_token_env: PUSHOVER_API_TOKEN
  max_per_day: 5
  max_per_ticker_per_week: 2
  resurface_score_delta: 0.10
  market_hours_only: true
  cadence_minutes: 15
```

**Out of scope for Cluster 3.** Two-way interaction (replying to a
notification to act on it), mobile app, web push, SMS via paid
gateways. Email + Pushover + Slack covers the single-user case
defined in §1.

### 13.4 Sequencing and definition of done

Cluster 1 ships first; it is independently useful (run the gate
manually after `scan`, eyeball the output) and unblocks Clusters 2
and 3.

Cluster 2 ships second. Without it, the conviction gate's
setup-validated check is a stub that always passes. With it, the gate
is honest.

Cluster 3 ships third. Without 1 and 2, notifications would either be
noisy (no gate) or dishonest (no validation). With 1 and 2 in place, 3
is mostly plumbing.

**Definition of done for the roadmap as a whole.**

- `paper watch --once` end-to-end: scan → ideas → conviction → notify,
  with a verified push notification on a real device.
- `conviction_log` and `notifications` tables populated and queryable
  from the dashboard.
- A new dashboard tab — **Alerts** — showing recent notifications, the
  gate verdicts for each, ack/snooze controls, and a per-setup
  performance summary pulled from `setup_performance`.
- A walk-forward backtest re-run with the gate applied as a filter on
  entries, reported side-by-side with the ungated baseline. The
  comparison is the empirical answer to "did the filter help?" — if
  the gated version is worse, the gate is wrong and the roadmap has
  failed its own validation.
- §11 status updated: "no alerting" and "factor model not fused" both
  moved to "shipped."

### 13.5 Deliberate non-additions

The following are *not* in this roadmap and should be pushed back on
if proposed:

- **Multi-asset expansion (crypto, futures, FX).** Different data
  vendors, different risk models, different setups. Out of scope.
- **Real-time tick-level signals.** Cadence is minutes, not
  milliseconds. Non-goal #2.
- **ML-discovered setups.** Every setup in the library is
  hypothesis-driven, hand-written, and reviewed. Auto-discovered
  setups invite the overfitting failure mode this project was built
  to avoid.
- **Sharing notifications externally / publishing picks.** The
  audience (§1) is one user. Multi-user introduces compliance
  questions the project has explicitly declined to engage with
  (non-goal #7).
- **Long-term (months to years) holding setups.** Honest validation
  requires point-in-time fundamentals the current data layer does not
  provide (§9). Revisit only after a paid vendor with point-in-time
  data is wired.

### 13.6 Cluster 4 — factor fusion into the live score

**Goal.** Make the factor composite a *first-class input* to the live
decision, not a parallel display. Cluster 1's factor-agreement gate is
a tactical fix — it asks the factor model only for a directional veto.
The strategic fix is fusing the factor composite into `composite_score`
so a ticker's score reflects every signal the project actually
believes in. §11 calls this "factor model not fused"; Cluster 1
mitigates it; this cluster closes it.

**Module.** `strategy/scorer.py` is extended to accept a
`factor_composite: float | None` and fold it in at a configured weight.
The factor composite continues to live in `strategy/factors/` — this is
a wiring change, not a relocation.

**Shape.** Three-way blend, configured at `strategy.factor_weight`
(default 0.30) alongside `strategy.sentiment_weight` (already 0.40).
The technical tier (trend / momentum / breakout) makes up the
remainder. When `factor_composite is None` (single-name scan,
watchlist too small to rank cross-sectionally, or backtest window with
insufficient peers) the blend renormalizes across the other two — the
same pattern sentiment uses when Reddit creds are missing.

**Universe problem.** Factors are cross-sectional by definition; they
need peers. `strategy/cross_section.py` already provides `zscore`,
`rank_pct`, and sector-neutralization. This cluster adds
`strategy/factors/universe.py` whose job is to assemble the ranking
universe for a given scan — by default the union of (a) the configured
watchlist and (b) a sector-matched peer set drawn from a configured
reference list. Sufficiently small universes
(`n < factors.min_universe`) skip the factor leg and the blend falls
back to a two-way mix.

**Score-explain.** `score_explain.py:explain_score()` gains a
`factor_contribution` field and the dashboard's Score Breakdown tab
gains a Factor row showing each underlying factor's z-score, weight,
and signed contribution. Same pattern as the existing sentiment row.

**Backtest implication.** The backtester already uses the factor
composite directly to score the universe at each rebalance. This
cluster makes the live score *match* the backtest's scoring pipeline —
fixing the long-standing seam where the system you backtest is not
the system you trade. The walk-forward report adds a side-by-side:
unfused (legacy) vs. fused (new) on the same window.

**Config.**

```yaml
strategy:
  factor_weight: 0.30
  sentiment_weight: 0.40   # existing, unchanged
factors:
  min_universe: 12
  peer_pool: [SPY_constituents]   # default; configurable
```

**Tests.** Synthetic fixture with controlled tech / sentiment / factor
inputs; verify the blend matches the documented formula, the
small-universe fallback fires correctly, and `explain_score` exposes
the factor contribution as a first-class row.

**Out of scope for Cluster 4.** Auto-tuned factor weights (smells like
overfitting). Factor *discovery* — the six factors remain hand-defined
per Cluster 2's discipline. Multi-period factor decay models. The
fusion is a weighted sum, not a learned function.

### 13.7 Cluster 5 — real-broker interface, read-only first

**Goal.** Close the obvious gap from §11 ("no real broker") while
preserving non-goal #3 ("no auto-trade real money without human
approval") *architecturally*, not as a config flag. The cluster ships
a real-broker connection that *cannot* place trades; placing trades
is a separate, later phase behind an explicit opt-in.

**Phase A — read-only sync.** A new `engine/brokers/` subpackage with
implementations of the existing `Broker` Protocol, but with
`submit_order` / `cancel_order` raising `BrokerReadOnlyError`. What
they *do* implement: `account_summary()`, `positions()`,
`transactions(since)`, and `quote(symbol)`. These power a new
dashboard tab — **Live (read-only)** — that displays the user's real
brokerage portfolio alongside the paper portfolio.

**Initial backends.**

- `engine/brokers/tradier.py` — OAuth, sandbox + production endpoints, REST.
- `engine/brokers/ibkr.py` — Client Portal Gateway (no TWS dependency), REST.
- `engine/brokers/alpaca.py` — bearer token, paper + live endpoints.

Selection via `brokers.live_backend` in config; credentials via env
vars, never written to disk. The existing `Broker.auto_approve`
default-False stays for the paper broker; the *type* of a
read-only broker simply does not expose order submission, so the
"don't auto-trade" property is enforced by the compiler, not by
a runtime check.

**Phase B — per-order approval flow.** A separate `LiveOrderRouter`
class wraps a non-read-only `Broker`. It takes a paper-engine
recommendation and stages it as a `pending_order` row. The user
approves via dashboard click *or* CLI
(`stockbot live approve <id>` / `live reject <id>`). Approval calls
`Broker.submit_order`; the rejected path is logged. Every action —
staged, approved, rejected, submitted, filled, errored — is a row in
a new `live_order_audit` table and an `audit_log` event. **Phase B is
not built in this cluster**, but the schema and dashboard plumbing
land here so Phase B is mostly a routing change.

**Reconciliation.** Once a live position exists,
`reporting/attribution.py` is extended with a `live_vs_paper` view:
for any ticker held in both, show paper P&L vs. live P&L, sized
differences, and any divergence in entry / exit timing. This is the
empirical answer to "would my system have done better?" — the live
book is the control, the paper book is the experiment, both running
on the same recommendations.

**Compliance touchpoints.** PDT (`ops/pdt.py`) and wash-sale
(`ops/wash_sale.py`) move from opt-in utilities to *required
pre-checks* for any live order, regardless of `compliance.*` toggles.
The toggles continue to govern paper reporting only. Hitting either
rule in the live path is a hard reject — no override path until a
future cluster considers one.

**Config.**

```yaml
brokers:
  live_backend: tradier         # tradier | ibkr | alpaca | null
  tradier:
    access_token_env: TRADIER_ACCESS_TOKEN
    account_id_env: TRADIER_ACCOUNT_ID
    sandbox: true
  read_only: true               # phase A; phase B flips this with opt-in
```

**Out of scope for Cluster 5.** Margin, options-level approvals (the
live broker may not have the same option permissions the paper engine
assumes), multi-account routing, tax-lot selection at submit time.
Equity-only first; options on a live broker are their own cluster.

### 13.8 Cluster 6 — book-level rebalancer

**Goal.** Move sizing from per-position to per-portfolio. Today,
`portfolio/sizing.py` decides each position's size in isolation; the
only book-level checks are guardrails and constraint caps.
`sizing.risk_parity_weights` exists but is not wired into
`engine/paper.step()`. This cluster wires it and adds the surrounding
scaffolding so risk parity / mean-variance / vol-target-at-book can
actually run.

**Module.** New `portfolio/rebalancer.py`, paralleling `sizing.py` in
role: pure function from
`(current_book, candidate_picks, constraints) → target_weights`.

**Algorithms.**

- `equal_risk_contribution` (risk parity, default) — Newton iteration
  on `sizing.risk_parity_weights`, but operating on the *combined* set
  of current positions and new picks rather than picks alone.
- `mean_variance_with_views` — Black-Litterman framing where the
  "views" are conviction-gate `ConvictionPick.confidence_band`s. Falls
  back to plain MV when no picks have bands.
- `vol_target_book` — scales the book so realized portfolio
  volatility hits `rebalance.target_vol`.

**Cadence.** The rebalancer runs on a configured cadence
(`rebalance.cadence_days`, default 5), not every cycle. Cluster 3's
notification layer fires for *new* picks; the rebalancer fires for
*existing* book drift. Both write to a new `rebalance_proposals`
table — same audit pattern as `recommendations`. The user approves or
rejects; only approved proposals translate to orders (paper or live).

**Interaction with conviction gate.** A position the rebalancer wants
to *grow* must still clear the conviction gate at current prices; the
gate is the bar for committing more capital, the rebalancer is the
bar for *how much*. A position the rebalancer wants to *shrink*
skips the gate — exits are always allowed.

**Constraints.** `portfolio/constraints.py` is the source of truth
for sector / exposure / beta / correlation limits; the rebalancer
takes it as input and treats it as a hard feasibility region.
Infeasible proposals are downgraded (e.g. "rebalance partially: hit
sector cap") and logged, not silently clipped.

**Backtest.** `backtest/engine.py` gains an optional rebalancer hook.
The walk-forward report compares (a) per-position sizing only
(current) vs. (b) per-position + book rebalance (new). The comparison
is the empirical answer to "did book-level sizing help?" — same
contract as the gated-vs-ungated comparison in §13.4.

**Config.**

```yaml
rebalance:
  enabled: false                  # default off; explicit opt-in
  algo: equal_risk_contribution
  cadence_days: 5
  target_vol: 0.15                # used by vol_target_book only
  max_turnover_per_rebalance: 0.20
```

**Out of scope for Cluster 6.** Tax-aware rebalancing (lot selection,
harvesting). Continuous (per-cycle) rebalancing — non-goal #2
territory. Optimization over option Greeks at the book level;
`greeks_agg.py` already reports, but optimizing on it is its own
problem.

### 13.9 Phase 2 sequencing and definition of done

Clusters 4–6 are explicitly **Phase 2** — they assume Phase 1
(Clusters 1–3) has shipped. Within Phase 2 the natural order is:

1. **Cluster 4 (factor fusion)** first. It changes what every
   downstream artifact means; running 5 or 6 before 4 means the live
   broker reflects a scoring model that is about to change.
2. **Cluster 6 (rebalancer)** second. It depends on the fused score
   for ideas-with-views but does not depend on a live broker.
3. **Cluster 5 (real-broker)** last. Highest blast radius; benefits
   from the longest in-paper soak. Phase A (read-only) can land in
   parallel with Cluster 4 or 6; Phase B (per-order approval) waits.

**Definition of done for Phase 2.**

- Live score and backtest score use the same fused composite.
  `explain_score` shows tech / sentiment / factor contributions and
  the exact arithmetic.
- A rebalance proposal — generated, audited, approved or rejected —
  exists in the SQLite db, and the walk-forward report shows the
  rebalanced book's metrics next to the unrebalanced baseline.
- A live brokerage account is connected in read-only mode; the
  dashboard's Live tab shows real positions next to paper positions;
  `live_vs_paper` attribution reports for at least one ticker held
  in both.
- §11 status updated: "factor model not fused," "no portfolio
  optimizer," and "no real broker" all move to "shipped" (the latter
  as "shipped read-only").
