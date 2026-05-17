# Non-Goals

Things this bot explicitly will not do. Listed up front so expectations don't
drift over time.

## 1. Predict short-term price moves with claimed high accuracy.

If it could, it wouldn't be running on a kitchen laptop. Any factor or signal
in this codebase is a noisy edge over a long horizon, not a daily-prediction
engine. Treat headline backtest Sharpes as upper bounds — live results
typically degrade meaningfully.

## 2. Compete on execution speed.

Retail infrastructure cannot beat HFT or institutional flow. Strategies whose
edge depends on millisecond execution, queue position, or co-located feeds are
out of scope. The order builder produces limit orders intended for human
review, not microsecond placement.

## 3. Auto-trade real money without human approval.

Even when a real broker is wired in, the default mode is `auto_approve=False`:
orders are queued for the user to confirm. Auto-execution requires explicit,
per-order opt-in and a multi-step override for any policy breach.

## 4. Treat backtested Sharpe as expected live Sharpe.

Sections 3 and 4 of the requirements (validation + risk) exist precisely
because in-sample performance lies. The walk-forward harness, deflated Sharpe,
and stress tests are mandatory before sizing any strategy.

## 5. Recommend option structures whose risk the user cannot articulate.

The Thesis object enforces a falsifiable hypothesis and stated max loss. If
those can't be filled in, the strategy doesn't ship. No naked short calls, no
unhedged short puts in size, no "I'll figure it out if it moves against me."

## 6. Trade on insider, non-public, or unauthorized information.

This bot consumes only public market data, public fundamentals, public
sentiment (Reddit/StockTwits), and public macro. Any future data source must
clear that bar.

## 7. Provide investment advice.

The output of this bot is research, not advice. It is a tool to help a
disciplined user execute a documented strategy — not a substitute for a
licensed advisor, accountant, or your own judgment.

## 8. Mask losses.

Every recommendation is logged, executed or not. Every override is logged.
Every config change is hashed and snapshotted. The system favors honest
post-mortems over flattering dashboards.
