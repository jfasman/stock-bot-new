from datetime import datetime, timedelta, timezone

from stockbot.config import Config
from stockbot.portfolio.targets import evaluate


def _cfg(target=0.40):
    return Config(raw={"portfolio": {"target_annual_return": target}})


def test_on_track_when_meeting_target():
    started = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    # 40% annual ≈ daily 0.000925 → 30 days ≈ ~2.8% growth
    status = evaluate(_cfg(), starting_cash=100_000, started_at=started, equity=103_000)
    assert status.on_track is True
    assert status.days_elapsed == 30
    assert status.target_annual == 0.40


def test_off_track_when_lagging():
    started = (datetime.now(timezone.utc) - timedelta(days=180)).isoformat()
    status = evaluate(_cfg(), starting_cash=100_000, started_at=started, equity=95_000)
    assert status.on_track is False
    assert status.actual_return < 0
