# stock-bot

Paper-trading research bot. Scores a watchlist with technical signals
plus Reddit/StockTwits sentiment, generates swing-trade and options
ideas, runs entirely in simulation against a SQLite-backed virtual
portfolio. **Not** an auto-trader or advisor — see
[docs/NON_GOALS.md](docs/NON_GOALS.md).

## Build & test

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install pytest

pytest                              # all tests in tests/ (~83)
python -m stockbot.main --help      # CLI surface
streamlit run dashboard.py          # Streamlit UI on :8501
```

State lives in `data/stockbot.db` (SQLite). Delete it to reset the sim.

## Where things are

Module map and scoring details live in
[docs/PROJECT_CONTEXT.md](docs/PROJECT_CONTEXT.md) — read it first when
scoping a new feature. Highlights:

- `stockbot/main.py` — Click CLI: `scan`, `paper run/open/close`,
  `report`, `rec-log`, `risk-report`, `stress`, `backtest`, `explain`.
- `stockbot/strategy/scorer.py` — the **live** score
  (tech + sentiment blend); see also `score_explain.py`.
- `stockbot/strategy/factors/` — the parallel factor model. Not yet
  fused into the live score (roadmap §13.6).
- `stockbot/strategy/ideas.py` — turns scores into `Idea[]`
  (equity / call / put / inverse-ETF / leveraged-long-ETF).
- `stockbot/engine/paper.py` — the cycle:
  size → thesis → guardrails → audit → journal.
- `stockbot/portfolio/` — sizing (Kelly / vol-target / risk-parity),
  VaR/CVaR, stress, Greeks, constraints.
- `stockbot/backtest/` — event-driven, point-in-time,
  deflated-Sharpe-aware.
- `stockbot/ops/` — recommendation log, audit, config snapshots,
  guardrails, wash-sale, PDT.
- `dashboard.py` — 9-tab Streamlit UI.

## Roadmap

Active roadmap is in [roadmap-section.md](roadmap-section.md). Phase 1
is Clusters 1–3 (conviction gate, setup library, notifications). Phase
2 is Clusters 4–6 (factor fusion, real-broker read-only, book-level
rebalancer). When proposing work, pick a cluster or explicitly justify
why it doesn't belong to one.

## Discipline norms

These are project-specific and not negotiable without an explicit
discussion:

1. **Every recommendation is logged** — executed or not
   (`ops/recommendation_log.py`). Don't add a code path that emits a
   trade idea without writing to `recommendations`.
2. **Every config change is hashed and snapshotted**
   (`ops/config_snapshot.py`). A thesis must be reproducible from
   `{thesis, config_hash}`.
3. **Guardrails breach → multi-step override + audit_log**
   (`ops/guardrails.py`). Don't add a flag that bypasses the override
   path.
4. **Point-in-time data for backtests.**
   `backtest/engine._slice_as_of()` is the contract. Don't read
   "current" data inside the backtest loop.
5. **Theses are falsifiable.** `strategy/thesis.py:Thesis` carries
   invalidation triggers. New idea types must specify what would
   invalidate them.
6. **Auto-trade is gated by type, not by flag.** Read-only Brokers
   don't expose `submit_order` (roadmap §13.7); preserve that
   property.
7. **Non-goals are real.** Before adding a feature, check
   `docs/NON_GOALS.md` — multi-asset, tick-level signals,
   ML-discovered setups, and a few others are out of scope on
   purpose.

## Code style

- Python 3.10+ (tested on 3.13). Type hints on new public functions.
- Dataclasses for value objects (`Thesis`, `Idea`, `ScoreBreakdown`,
  `ConvictionPick` when it lands).
- Pure functions in `strategy/` and `portfolio/sizing.py` — no side
  effects, fully unit-tested.
- SQLite access goes through `portfolio/store.py`; don't open new
  connections elsewhere.
- New modules belong inside an existing top-level subpackage (`data`,
  `sentiment`, `strategy`, `portfolio`, `engine`, `backtest`, `ops`,
  `reporting`). Create a new subpackage only with a written
  rationale.

## Tests

- `tests/` mirrors the module tree; one `test_<module>.py` per source
  module.
- Backtest tests use synthetic price series, not network calls
  (`tests/conftest.py` has fixtures).
- New features land with their tests in the same PR, not as a
  follow-up.
- CI runs the same `pytest` invocation as local via
  `.github/workflows/test.yml`.

## Commits & PRs

- Feature branches: `feat/<short-name>` (e.g. `feat/conviction-gate`);
  fixes: `fix/<short-name>`.
- One roadmap cluster per branch when working through
  `roadmap-section.md`.
- Commit subject ≤ 70 chars; body explains *why*. Reference the
  roadmap cluster (e.g. "roadmap §13.1") when relevant.
- PRs land via `gh pr create`; CI must pass before merge.
