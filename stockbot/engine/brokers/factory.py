"""Pick a `LiveBroker` implementation from config.

Selection is via `brokers.live_backend` (str | null). Credentials live in
env vars whose names are themselves configured (e.g. `access_token_env`) so
the project never sees the credential values directly.

`get_live_broker(cfg)` returns None when:
  - `brokers.live_backend` is missing or null
  - the configured env vars aren't set (treated as "no broker available"
    rather than raising — the dashboard tab degrades to an empty-state)

Use `get_live_broker(cfg, strict=True)` to raise instead, when the caller
wants a hard error (e.g. an explicit `stockbot live status` CLI invocation).
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from ...config import Config
from .base import LiveBroker

log = logging.getLogger(__name__)


class LiveBrokerNotConfigured(RuntimeError):
    """Raised by `get_live_broker(..., strict=True)` when no backend is wired."""


def get_live_broker(cfg: Config, *, strict: bool = False) -> Optional[LiveBroker]:
    brokers_cfg = cfg.raw.get("brokers", {}) if hasattr(cfg, "raw") else {}
    backend = brokers_cfg.get("live_backend")
    if not backend:
        if strict:
            raise LiveBrokerNotConfigured("`brokers.live_backend` is not set.")
        return None

    backend = str(backend).lower()
    sub_cfg = brokers_cfg.get(backend, {}) or {}

    try:
        if backend == "alpaca":
            from .alpaca import AlpacaLiveBroker
            return _instantiate(AlpacaLiveBroker, sub_cfg, strict=strict)
        if backend == "tradier":
            from .tradier import TradierLiveBroker
            return _instantiate(TradierLiveBroker, sub_cfg, strict=strict)
        if backend == "ibkr":
            from .ibkr import IbkrLiveBroker
            return _instantiate(IbkrLiveBroker, sub_cfg, strict=strict)
    except LiveBrokerNotConfigured:
        if strict:
            raise
        return None

    msg = f"Unknown brokers.live_backend '{backend}' — expected one of: alpaca, tradier, ibkr."
    if strict:
        raise LiveBrokerNotConfigured(msg)
    log.warning(msg)
    return None


def _instantiate(cls, sub_cfg: dict, *, strict: bool) -> Optional[LiveBroker]:
    """Resolve env-var-named credentials and instantiate. Returns None or
    raises depending on `strict` if any required env var is empty."""
    creds = _resolve_env_creds(sub_cfg)
    missing = [k for k, v in creds.items() if not v]
    if missing:
        msg = (
            f"{cls.__name__}: required env vars are not set: {missing}. "
            f"Check `brokers.{sub_cfg.get('_backend_name', '?')}` in config.yaml."
        )
        if strict:
            raise LiveBrokerNotConfigured(msg)
        log.info(msg)
        return None
    return cls(**creds, **{k: v for k, v in sub_cfg.items() if not k.endswith("_env")})


def _resolve_env_creds(sub_cfg: dict) -> dict:
    """Read keys ending in `_env` as env-var names, return them as the
    corresponding param without the suffix.

    Example: ``{"access_token_env": "ALPACA_KEY"}`` resolves to
    ``{"access_token": os.environ["ALPACA_KEY"]}``.
    """
    out: dict = {}
    for k, v in sub_cfg.items():
        if k.endswith("_env") and isinstance(v, str):
            param_name = k[: -len("_env")]
            out[param_name] = os.environ.get(v)
    return out
