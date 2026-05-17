from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from ..portfolio.store import connect, init_db
from .audit import log_event


@dataclass
class GuardrailDecision:
    allow: bool
    reasons: List[str]
    requires_override: bool = False

    def merge(self, other: "GuardrailDecision") -> "GuardrailDecision":
        return GuardrailDecision(
            allow=self.allow and other.allow,
            reasons=self.reasons + other.reasons,
            requires_override=self.requires_override or other.requires_override,
        )


def _set_state(key: str, value: dict) -> None:
    init_db()
    with connect() as db:
        db.execute(
            """INSERT INTO guardrail_state (key, value, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
            (key, json.dumps(value), datetime.utcnow().isoformat()),
        )


def _get_state(key: str) -> Optional[dict]:
    init_db()
    with connect() as db:
        row = db.execute("SELECT value FROM guardrail_state WHERE key = ?", (key,)).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["value"])
    except Exception:
        return None


def trigger_loss_cooldown(loss_pct: float, cooldown_hours: int = 24) -> None:
    """Engage a cooldown after a drawdown event. Call after marking a loss day."""
    until = (datetime.now(timezone.utc) + timedelta(hours=cooldown_hours)).isoformat()
    _set_state("loss_cooldown", {"until": until, "loss_pct": loss_pct})
    log_event("guardrail.cooldown_engaged", {"until": until, "loss_pct": loss_pct})


def loss_cooldown_active() -> tuple[bool, Optional[str]]:
    state = _get_state("loss_cooldown")
    if not state:
        return False, None
    try:
        until = datetime.fromisoformat(state["until"])
    except Exception:
        return False, None
    if datetime.now(timezone.utc) < until:
        return True, state["until"]
    return False, None


def check_pre_trade(
    *,
    proposed_weight: float,
    portfolio_concentration: float,
    max_concentration: float = 0.30,
    leverage: float = 1.0,
    max_leverage: float = 1.0,
) -> GuardrailDecision:
    """Hard limits the bot enforces on the user. Breaches require multi-step override."""
    cooldown, until = loss_cooldown_active()
    reasons: List[str] = []
    requires_override = False
    allow = True

    if cooldown:
        reasons.append(f"loss cooldown active until {until}")
        allow = False
        requires_override = True

    if portfolio_concentration + proposed_weight > max_concentration:
        reasons.append(
            f"would push concentration to {portfolio_concentration + proposed_weight:.2%} > "
            f"max {max_concentration:.2%}"
        )
        allow = False
        requires_override = True

    if leverage > max_leverage:
        reasons.append(f"leverage {leverage:.2f}x > max {max_leverage:.2f}x")
        allow = False
        requires_override = True

    return GuardrailDecision(allow=allow, reasons=reasons, requires_override=requires_override)


def record_override(reason: str, actor: str = "user", confirmation_steps: int = 2) -> bool:
    """Record a multi-step override. The caller must have collected `confirmation_steps`
    independent confirmations from the user before invoking this.
    """
    if confirmation_steps < 2:
        log_event("guardrail.override_rejected", {"reason": reason, "actor": actor})
        return False
    log_event("guardrail.override_granted", {"reason": reason, "actor": actor, "steps": confirmation_steps})
    return True


def deviation_prompt(systematic: dict, manual: dict) -> str:
    """Build the mandatory prompt shown to the user when they deviate from a
    systematic recommendation. The system records the response in the audit log.
    """
    diffs = []
    for k in set(systematic) | set(manual):
        if systematic.get(k) != manual.get(k):
            diffs.append(f"  {k}: system={systematic.get(k)!r} -> manual={manual.get(k)!r}")
    body = "\n".join(diffs) if diffs else "  (no differences detected)"
    return (
        "You are deviating from the systematic recommendation:\n"
        f"{body}\n"
        "Type the reason for the deviation. It will be recorded in the audit log."
    )
