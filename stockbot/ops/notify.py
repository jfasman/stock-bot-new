"""Notification dispatch. Spec: roadmap §13.3.

A `Notifier` is a small Protocol — one method `send(payload)` returning
True iff delivery succeeded. The roadmap names five implementations
(stdout, file, pushover, slack, email); this module ships the two with
no network/credential dependencies. The others land in a follow-up
once the dispatch path is proven.

Notifiers are pure of side effects beyond their own channel: no DB
writes here. Persistence is the caller's job (ops/notification_log.py)
so the audit trail records every attempt regardless of delivery
outcome.
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Protocol, runtime_checkable

from ..config import DATA_DIR
from ..strategy.conviction import ConvictionPick


@dataclass(frozen=True)
class NotificationPayload:
    """Bounded summary of a ConvictionPick. Roadmap §13.3 lists the
    fields: ticker, direction, instrument, entry zone, stop, target,
    matched setup name + expectancy, time_to_act, deep link.

    Keep the headline under ~100 chars so it fits a push preview;
    body carries the rest.
    """
    ticker: str
    direction: str
    instrument: str
    score: float
    matched_setup: str | None
    setup_expectancy: float | None
    time_to_act: str
    confidence_band: tuple[float, float]
    deep_link: str | None = None

    @property
    def headline(self) -> str:
        side = self.direction.upper()
        setup = f" · {self.matched_setup}" if self.matched_setup else ""
        return f"{self.ticker} {side} {self.instrument} (score {self.score:+.2f}){setup}"

    @property
    def body(self) -> str:
        lines = [
            f"Score: {self.score:+.3f}",
            f"Band: {self.confidence_band[0]:+.2f} → {self.confidence_band[1]:+.2f}",
            f"Time-to-act: {self.time_to_act}",
        ]
        if self.matched_setup:
            exp = f" (E={self.setup_expectancy:+.3f}R)" if self.setup_expectancy is not None else ""
            lines.append(f"Setup: {self.matched_setup}{exp}")
        if self.deep_link:
            lines.append(f"More: {self.deep_link}")
        return "\n".join(lines)


def payload_from_pick(pick: ConvictionPick, deep_link: str | None = None) -> NotificationPayload:
    """Build a NotificationPayload from a ConvictionPick. Pure; no I/O."""
    matched = pick.idea.reasons  # noqa: F841 — placeholder for setup info if Idea later carries it
    # ConvictionPick doesn't currently carry the matched setup name (the gate's
    # matched_setup lives on GateContext, not the pick). The caller can pass it
    # via deep_link or extend ConvictionPick later. For now, derive what we can.
    return NotificationPayload(
        ticker=pick.idea.ticker,
        direction=pick.idea.direction,
        instrument=pick.idea.instrument,
        score=pick.idea.score,
        matched_setup=None,                                  # to be wired with pick extension
        setup_expectancy=None,
        time_to_act=pick.time_to_act.value,
        confidence_band=pick.confidence_band,
        deep_link=deep_link,
    )


@runtime_checkable
class Notifier(Protocol):
    name: str
    def send(self, payload: NotificationPayload) -> bool: ...


@dataclass(frozen=True)
class StdoutNotifier:
    """Prints a one-line headline plus body to stdout. Always 'succeeds.'
    Useful for development and as a guaranteed fallback channel.
    """
    name: str = "stdout"

    def send(self, payload: NotificationPayload) -> bool:
        print(f"[stockbot:notify] {payload.headline}", file=sys.stdout)
        for line in payload.body.splitlines():
            print(f"                  {line}", file=sys.stdout)
        return True


@dataclass(frozen=True)
class FileNotifier:
    """Appends one JSON line per notification to a configured path.
    Default path is `data/notifications.jsonl`. Replayable audit log;
    survives across processes.
    """
    name: str = "file"
    path: Path = DATA_DIR / "notifications.jsonl"

    def send(self, payload: NotificationPayload) -> bool:
        record = {
            "ts": datetime.utcnow().isoformat(),
            **asdict(payload),
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as f:
                f.write(json.dumps(record) + "\n")
            return True
        except OSError:
            return False


def build_notifiers(cfg) -> list[Notifier]:
    """Construct the active notifiers from `cfg.notifications.backend`.

    Unknown backends are skipped with a warning rather than raising —
    one misspelled config key shouldn't break the watch loop.
    """
    import logging
    log = logging.getLogger(__name__)

    section = getattr(cfg, "notifications", None) or cfg.raw.get("notifications", {})
    backends = section.get("backend", ["stdout"]) if isinstance(section, dict) else ["stdout"]
    if isinstance(backends, str):
        backends = [backends]

    out: list[Notifier] = []
    for name in backends:
        if name == "stdout":
            out.append(StdoutNotifier())
        elif name == "file":
            out.append(FileNotifier())
        else:
            log.warning("notifier backend %r is not implemented yet — skipping", name)
    if not out:
        out.append(StdoutNotifier())                          # always have at least one channel
    return out


def dispatch(notifiers: Iterable[Notifier], payload: NotificationPayload) -> dict[str, bool]:
    """Send `payload` to every notifier. Returns {name: delivered_ok}.

    A notifier raising on send is recorded as `False`, not propagated —
    one broken backend shouldn't suppress the others.
    """
    import logging
    log = logging.getLogger(__name__)

    results: dict[str, bool] = {}
    for n in notifiers:
        try:
            results[n.name] = n.send(payload)
        except Exception as exc:
            log.warning("notifier %s raised: %s", n.name, exc)
            results[n.name] = False
    return results
