from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from tokenledger.config import Machine, Settings
from tokenledger.models import CaptureResult, CostQuality, DayUsage, Scope, SeriesKey
from tokenledger.store import connect, migrate
from tokenledger.store import repo

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def settings() -> Settings:
    return Settings(timezone="Europe/London", grace_days=3, machine=Machine("test-mac", "test", "darwin"))


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "ledger.sqlite")
    migrate(c)
    yield c
    c.close()


SERIES = SeriesKey("codexbar", "claude", "test-mac", Scope.MACHINE)


def utc(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def usage(day: str, total: int, model: str = "*", cost: int | None = None) -> DayUsage:
    quality = CostQuality.LIST_PRICE if cost is not None else CostQuality.NONE
    return DayUsage(day=day, model=model, total=total, cost_micros=cost, cost_quality=quality)


def capture(rows, window=None) -> CaptureResult:
    start, end = window if window else (None, None)
    return CaptureResult(
        series=SERIES, rows=rows, window_start=start, window_end=end, tool_version="t", raw_name="t", raw=b""
    )


def run_capture(conn, settings, result, now: str):
    from tokenledger import ledger

    with repo.transaction(conn):
        cid = repo.start_capture(conn, "usage", result.series.source, None, now)
        return ledger.apply_capture(conn, result, settings=settings, now_utc=utc(now), capture_id=cid)
