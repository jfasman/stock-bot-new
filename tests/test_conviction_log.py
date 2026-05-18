"""Tests for the conviction audit log. Spec: roadmap §13.1 "Logging"."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from stockbot.ops.conviction_log import (
    last_notified_at,
    log_evaluation,
    recent,
)
from stockbot.strategy.conviction import (
    ConvictionPick,
    GateResult,
    TimeToAct,
)
from stockbot.strategy.ideas import Idea


_ALL_GATES = ("score", "factor_agreement", "regime", "setup_validated", "cooldown", "data_quality")


def _idea(ticker: str = "AAPL", score: float = 0.80) -> Idea:
    return Idea(
        ticker=ticker, direction="long", instrument="equity",
        score=score, last_price=200.0, rsi=55.0,
        reasons=["test"], sentiment_net=0.0, sentiment_confidence=0.0,
    )


def _all_pass_verdicts() -> dict[str, GateResult]:
    return {name: GateResult(True, f"{name} ok") for name in _ALL_GATES}


def _mixed_verdicts() -> dict[str, GateResult]:
    return {
        "score": GateResult(True, "ok"),
        "factor_agreement": GateResult(False, "disagreement (live=+0.80, factor=-0.30)"),
        "regime": GateResult(True, "ok"),
        "setup_validated": GateResult(True, "ok"),
        "cooldown": GateResult(True, "ok"),
        "data_quality": GateResult(True, "ok"),
    }


def _pick(idea: Idea) -> ConvictionPick:
    return ConvictionPick(
        idea=idea,
        gates_passed=_ALL_GATES,
        confidence_band=(0.4, 0.80),
        time_to_act=TimeToAct.OPEN_TOMORROW,
    )


def test_log_evaluation_pass_persists_pick_and_overall_flag():
    idea = _idea()
    row_id = log_evaluation(idea, _all_pass_verdicts(), pick=_pick(idea))
    assert row_id > 0

    rows = recent()
    assert len(rows) == 1
    row = rows[0]
    assert row["ticker"] == "AAPL"
    assert row["overall_passed"] == 1
    assert row["score_passed"] == 1
    assert row["pick_json"] is not None
    pick_payload = json.loads(row["pick_json"])
    assert pick_payload["time_to_act"] == "open_tomorrow"
    assert pick_payload["confidence_band"] == [0.4, 0.80]


def test_log_evaluation_fail_persists_null_pick_and_per_gate_flags():
    log_evaluation(_idea(), _mixed_verdicts(), pick=None)
    row = recent()[0]
    assert row["overall_passed"] == 0
    assert row["pick_json"] is None
    assert row["factor_agreement_passed"] == 0
    assert row["score_passed"] == 1                # other gates still recorded as pass
    # Full verdict map round-trips with reasons intact.
    verdicts = json.loads(row["verdicts_json"])
    assert verdicts["factor_agreement"]["reason"].startswith("disagreement")


def test_last_notified_at_returns_none_when_never_passed():
    log_evaluation(_idea("AAPL"), _mixed_verdicts(), pick=None)  # rejected
    assert last_notified_at("AAPL") is None
    assert last_notified_at("UNKNOWN") is None


def test_last_notified_at_returns_most_recent_pass():
    idea = _idea("AAPL")
    older = datetime(2026, 5, 17, 10, 0)
    newer = datetime(2026, 5, 17, 14, 0)
    log_evaluation(idea, _all_pass_verdicts(), pick=_pick(idea), ts=older)
    log_evaluation(idea, _all_pass_verdicts(), pick=_pick(idea), ts=newer)
    # A rejected row in between must not anchor the cooldown.
    log_evaluation(idea, _mixed_verdicts(), pick=None, ts=newer + timedelta(minutes=30))

    assert last_notified_at("AAPL") == newer


def test_last_notified_at_is_per_ticker():
    aapl = _idea("AAPL")
    msft = _idea("MSFT")
    aapl_ts = datetime(2026, 5, 17, 10, 0)
    msft_ts = datetime(2026, 5, 17, 14, 0)
    log_evaluation(aapl, _all_pass_verdicts(), pick=_pick(aapl), ts=aapl_ts)
    log_evaluation(msft, _all_pass_verdicts(), pick=_pick(msft), ts=msft_ts)

    assert last_notified_at("AAPL") == aapl_ts
    assert last_notified_at("MSFT") == msft_ts
    # Case-insensitive lookup mirrors generate_ideas's uppercase normalization.
    assert last_notified_at("aapl") == aapl_ts


def test_recent_orders_newest_first_and_respects_limit():
    idea = _idea()
    for i in range(5):
        log_evaluation(idea, _all_pass_verdicts(), pick=_pick(idea),
                       ts=datetime(2026, 5, 17, 10, i))
    rows = recent(limit=3)
    assert len(rows) == 3
    assert rows[0]["id"] > rows[1]["id"] > rows[2]["id"]


def test_log_evaluation_records_config_hash_when_provided():
    log_evaluation(_idea(), _all_pass_verdicts(), pick=_pick(_idea()),
                   config_hash="abc123")
    row = recent()[0]
    assert row["config_hash"] == "abc123"
