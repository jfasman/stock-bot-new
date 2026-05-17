from __future__ import annotations

import os
from typing import Dict, Type

from .base import DataVendor

_REGISTRY: Dict[str, Type[DataVendor]] = {}
_DEFAULT_NAME = "yfinance"


def register_vendor(name: str, cls: Type[DataVendor]) -> None:
    _REGISTRY[name.lower()] = cls


def list_vendors() -> list[str]:
    return sorted(_REGISTRY.keys())


def get_vendor(name: str | None = None) -> DataVendor:
    """Resolve a vendor by name (or env var STOCKBOT_VENDOR, or default)."""
    chosen = (name or os.environ.get("STOCKBOT_VENDOR") or _DEFAULT_NAME).lower()
    if chosen not in _REGISTRY:
        # Lazy-import default to avoid import cycles
        from . import yfinance_vendor  # noqa: F401
        from . import polygon_stub  # noqa: F401
        from . import alphavantage_stub  # noqa: F401
        from . import iex_stub  # noqa: F401
    if chosen not in _REGISTRY:
        raise KeyError(f"Unknown vendor '{chosen}'. Available: {list_vendors()}")
    return _REGISTRY[chosen]()
