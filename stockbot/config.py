from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "stockbot.db"
CACHE_DIR = DATA_DIR / "cache"


@dataclass
class Config:
    raw: Dict[str, Any]

    @property
    def portfolio(self) -> Dict[str, Any]:
        return self.raw.get("portfolio", {})

    @property
    def risk(self) -> Dict[str, Any]:
        return self.raw.get("risk", {})

    @property
    def strategy(self) -> Dict[str, Any]:
        return self.raw.get("strategy", {})

    @property
    def options(self) -> Dict[str, Any]:
        return self.raw.get("options", {})

    @property
    def sentiment(self) -> Dict[str, Any]:
        return self.raw.get("sentiment", {})

    @property
    def watchlist(self) -> List[str]:
        return [s.upper() for s in self.raw.get("watchlist", [])]


def load_config(path: str | Path | None = None) -> Config:
    """Load config.yaml from the project root (or supplied path) and .env credentials."""
    load_dotenv(ROOT / ".env", override=False)
    config_path = Path(path) if path else ROOT / "config.yaml"
    with open(config_path, "r") as f:
        raw = yaml.safe_load(f) or {}
    DATA_DIR.mkdir(exist_ok=True)
    CACHE_DIR.mkdir(exist_ok=True)
    return Config(raw=raw)


def env(key: str, default: str | None = None) -> str | None:
    val = os.environ.get(key, default)
    return val if val else default
