import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def isolate_db(monkeypatch, tmp_path):
    """Point the SQLite store at a temp file so tests don't touch the real db."""
    db_file = tmp_path / "test.db"
    import stockbot.config as cfg_mod
    monkeypatch.setattr(cfg_mod, "DB_PATH", db_file)
    import stockbot.portfolio.store as store
    monkeypatch.setattr(store, "DB_PATH", db_file)
    yield
