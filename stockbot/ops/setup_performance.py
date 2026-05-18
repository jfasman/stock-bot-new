"""Walk-forward performance store for the setup library. Spec:
roadmap §13.2 "Validation".

The conviction gate's `setup_validated` check queries this table
through `get_performance(name)`. Population is the backtest's job
(roadmap §13.2 — "regenerated on every backtest run"); until that
integration lands, rows can be seeded for testing via
`upsert_performance` directly or the planned CLI helper.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from ..portfolio.store import connect, init_db


@dataclass(frozen=True)
class SetupPerformance:
    setup_name: str
    direction: str
    n_trades: int
    win_rate: float
    avg_r: float
    expectancy: float
    sharpe: float
    last_validated_at: datetime


def upsert_performance(perf: SetupPerformance) -> None:
    """Replace the row for `perf.setup_name`. Idempotent."""
    init_db()
    with connect() as db:
        db.execute(
            """INSERT INTO setup_performance
               (setup_name, direction, n_trades, win_rate, avg_r, expectancy, sharpe, last_validated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(setup_name) DO UPDATE SET
                   direction=excluded.direction,
                   n_trades=excluded.n_trades,
                   win_rate=excluded.win_rate,
                   avg_r=excluded.avg_r,
                   expectancy=excluded.expectancy,
                   sharpe=excluded.sharpe,
                   last_validated_at=excluded.last_validated_at""",
            (
                perf.setup_name, perf.direction, perf.n_trades,
                perf.win_rate, perf.avg_r, perf.expectancy,
                perf.sharpe, perf.last_validated_at.isoformat(),
            ),
        )


def get_performance(setup_name: str) -> Optional[SetupPerformance]:
    init_db()
    with connect() as db:
        row = db.execute(
            "SELECT * FROM setup_performance WHERE setup_name = ?",
            (setup_name,),
        ).fetchone()
    return _row_to_perf(row) if row else None


def all_performance() -> list[SetupPerformance]:
    init_db()
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM setup_performance ORDER BY expectancy DESC"
        ).fetchall()
    return [_row_to_perf(r) for r in rows]


def _row_to_perf(row) -> SetupPerformance:
    return SetupPerformance(
        setup_name=row["setup_name"],
        direction=row["direction"],
        n_trades=int(row["n_trades"]),
        win_rate=float(row["win_rate"]),
        avg_r=float(row["avg_r"]),
        expectancy=float(row["expectancy"]),
        sharpe=float(row["sharpe"]),
        last_validated_at=datetime.fromisoformat(row["last_validated_at"]),
    )
