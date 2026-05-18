"""Tests for engine/brokers/factory.py — backend selection from config."""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from stockbot.config import Config
from stockbot.engine.brokers.factory import LiveBrokerNotConfigured, get_live_broker


def _cfg(brokers_cfg: dict | None) -> Config:
    return Config(raw={"brokers": brokers_cfg} if brokers_cfg else {})


def test_returns_none_when_brokers_block_missing():
    assert get_live_broker(_cfg(None)) is None


def test_returns_none_when_live_backend_is_null():
    assert get_live_broker(_cfg({"live_backend": None})) is None


def test_strict_raises_when_no_backend():
    with pytest.raises(LiveBrokerNotConfigured):
        get_live_broker(_cfg(None), strict=True)


def test_returns_none_for_unknown_backend():
    assert get_live_broker(_cfg({"live_backend": "fictional"})) is None


def test_strict_raises_for_unknown_backend():
    with pytest.raises(LiveBrokerNotConfigured, match="Unknown"):
        get_live_broker(_cfg({"live_backend": "fictional"}), strict=True)


def test_returns_none_when_env_var_missing():
    """access_token_env points at MISSING_VAR; nothing in the env → None."""
    cfg = _cfg({
        "live_backend": "alpaca",
        "alpaca": {
            "access_token_env": "MISSING_ENV_VAR_A",
            "secret_key_env": "MISSING_ENV_VAR_B",
        },
    })
    with patch.dict(os.environ, {}, clear=False):
        # Make sure the names really aren't set.
        os.environ.pop("MISSING_ENV_VAR_A", None)
        os.environ.pop("MISSING_ENV_VAR_B", None)
        assert get_live_broker(cfg) is None


def test_strict_raises_when_env_var_missing():
    cfg = _cfg({
        "live_backend": "alpaca",
        "alpaca": {
            "access_token_env": "ABSENT_X", "secret_key_env": "ABSENT_Y",
        },
    })
    os.environ.pop("ABSENT_X", None)
    os.environ.pop("ABSENT_Y", None)
    with pytest.raises(LiveBrokerNotConfigured):
        get_live_broker(cfg, strict=True)


def test_alpaca_instantiated_when_env_vars_set():
    cfg = _cfg({
        "live_backend": "alpaca",
        "alpaca": {
            "access_token_env": "ALPACA_KEY_X",
            "secret_key_env": "ALPACA_SECRET_X",
            "paper": True,
        },
    })
    with patch.dict(os.environ, {"ALPACA_KEY_X": "k", "ALPACA_SECRET_X": "s"}):
        broker = get_live_broker(cfg)
    assert broker is not None
    assert broker.name == "alpaca"


def test_tradier_instantiated_when_env_vars_set():
    cfg = _cfg({
        "live_backend": "tradier",
        "tradier": {
            "access_token_env": "TRADIER_T",
            "account_id_env": "TRADIER_A",
            "sandbox": True,
        },
    })
    with patch.dict(os.environ, {"TRADIER_T": "t", "TRADIER_A": "VA001"}):
        broker = get_live_broker(cfg)
    assert broker is not None
    assert broker.name == "tradier"


def test_ibkr_instantiated_when_env_vars_set():
    cfg = _cfg({
        "live_backend": "ibkr",
        "ibkr": {"account_id_env": "IBKR_ACC"},
    })
    with patch.dict(os.environ, {"IBKR_ACC": "U123"}):
        broker = get_live_broker(cfg)
    assert broker is not None
    assert broker.name == "ibkr"


def test_backend_name_is_case_insensitive():
    cfg = _cfg({
        "live_backend": "ALPACA",
        "alpaca": {
            "access_token_env": "K", "secret_key_env": "S",
        },
    })
    with patch.dict(os.environ, {"K": "a", "S": "b"}):
        broker = get_live_broker(cfg)
    assert broker is not None
