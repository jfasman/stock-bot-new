"""Tests for the notifier protocol + stdout/file implementations.
Spec: roadmap §13.3."""
from __future__ import annotations

import json

from stockbot.config import Config
from stockbot.ops.notify import (
    FileNotifier,
    NotificationPayload,
    Notifier,
    StdoutNotifier,
    build_notifiers,
    dispatch,
    payload_from_pick,
)
from stockbot.strategy.conviction import ConvictionPick, TimeToAct
from stockbot.strategy.ideas import Idea


def _payload(ticker: str = "AAPL") -> NotificationPayload:
    return NotificationPayload(
        ticker=ticker, direction="long", instrument="equity",
        score=0.82, matched_setup="pullback_in_uptrend", setup_expectancy=0.25,
        time_to_act="open_tomorrow", confidence_band=(0.4, 0.82),
        deep_link="http://localhost:8501",
    )


def _idea() -> Idea:
    return Idea(
        ticker="AAPL", direction="long", instrument="equity", score=0.82,
        last_price=200.0, rsi=55.0, reasons=[], sentiment_net=0.0,
        sentiment_confidence=0.0,
    )


def _pick() -> ConvictionPick:
    return ConvictionPick(
        idea=_idea(),
        gates_passed=("score", "factor_agreement", "regime",
                      "setup_validated", "cooldown", "data_quality"),
        confidence_band=(0.4, 0.82),
        time_to_act=TimeToAct.OPEN_TOMORROW,
    )


def test_stdout_notifier_implements_protocol():
    assert isinstance(StdoutNotifier(), Notifier)


def test_stdout_notifier_returns_true_and_prints_headline(capsys):
    assert StdoutNotifier().send(_payload()) is True
    captured = capsys.readouterr()
    assert "AAPL" in captured.out
    assert "score" in captured.out.lower()


def test_file_notifier_appends_jsonl(tmp_path):
    path = tmp_path / "notifications.jsonl"
    n = FileNotifier(path=path)
    assert n.send(_payload("AAPL")) is True
    assert n.send(_payload("MSFT")) is True

    lines = path.read_text().strip().splitlines()
    assert len(lines) == 2
    rec0 = json.loads(lines[0])
    assert rec0["ticker"] == "AAPL"
    assert "ts" in rec0
    assert json.loads(lines[1])["ticker"] == "MSFT"


def test_dispatch_collects_per_backend_results():
    class Always(Notifier):
        name = "always"
        def send(self, payload):
            return True

    class Never(Notifier):
        name = "never"
        def send(self, payload):
            return False

    class Boom(Notifier):
        name = "boom"
        def send(self, payload):
            raise RuntimeError("kaboom")

    results = dispatch([Always(), Never(), Boom()], _payload())
    assert results == {"always": True, "never": False, "boom": False}


def test_build_notifiers_defaults_to_stdout_when_unconfigured():
    cfg = Config(raw={})
    notifiers = build_notifiers(cfg)
    assert len(notifiers) >= 1
    assert any(n.name == "stdout" for n in notifiers)


def test_build_notifiers_skips_unimplemented_backends_with_fallback():
    cfg = Config(raw={"notifications": {"backend": ["pushover", "slack"]}})
    notifiers = build_notifiers(cfg)
    # Both unsupported → fallback ensures at least stdout is present.
    assert any(n.name == "stdout" for n in notifiers)


def test_build_notifiers_picks_file_when_configured():
    cfg = Config(raw={"notifications": {"backend": ["file"]}})
    notifiers = build_notifiers(cfg)
    assert any(n.name == "file" for n in notifiers)


def test_payload_from_pick_round_trips_fields():
    payload = payload_from_pick(_pick(), deep_link="http://x")
    assert payload.ticker == "AAPL"
    assert payload.direction == "long"
    assert payload.score == 0.82
    assert payload.time_to_act == "open_tomorrow"
    assert payload.deep_link == "http://x"
    # Headline is short enough for a push preview.
    assert len(payload.headline) <= 120
