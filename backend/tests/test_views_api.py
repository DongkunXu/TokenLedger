from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from tokenledger import views
from tokenledger.api import create_app
from tokenledger.config import Paths
from tokenledger.models import CostQuality, DayUsage, Scope, SeriesKey

LP = CostQuality.LIST_PRICE

from conftest import capture as make_capture, run_capture


def seed(conn, settings):
    claude = SeriesKey("codexbar", "claude", "test-mac", Scope.MACHINE)
    codex_local = SeriesKey("codexbar", "codex", "test-mac", Scope.MACHINE)
    codex_acct = SeriesKey("codex-server", "codex", "account", Scope.ACCOUNT)
    now = "2026-09-27T10:00:00"
    for series, rows in (
        (claude, [DayUsage(day="2026-09-25", model="*", total=100, input_uncached=10, cache_read=80, cache_write=5, output=5, cost_micros=2_000_000, cost_quality=LP),
                  DayUsage(day="2026-09-26", model="*", total=50, input_uncached=5, cache_read=40, cache_write=3, output=2, cost_micros=1_000_000, cost_quality=LP),
                  DayUsage(day="2026-09-26", model="claude-x", total=50, cost_micros=1_000_000, cost_quality=LP)]),
        (codex_local, [DayUsage(day="2026-09-26", model="*", total=30, input_uncached=3, cache_read=27, output=0, cost_micros=500_000, cost_quality=LP)]),
        (codex_acct, [DayUsage(day="2026-09-26", model="*", total=40), DayUsage(day="2026-09-24", model="*", total=70)]),
    ):
        result = make_capture(rows)
        result.series = series
        run_capture(conn, settings, result, now)


def test_daily_combines_account_and_machine(conn, settings):
    seed(conn, settings)
    d = views.daily(conn, "2026-09-24", "2026-09-26")
    codex = next(p for p in d["providers"] if p["provider"] == "codex")["values"]
    assert codex[0]["tokens"] == 70 and codex[0]["cost_usd"] is None and codex[0]["basis"] == "account"
    assert codex[2]["tokens"] == 40 and codex[2]["machine_tokens"] == 30
    assert codex[2]["cost_usd"] == 0.5 and codex[2]["cost_tokens"] == 30
    claude = next(p for p in d["providers"] if p["provider"] == "claude")["values"]
    assert claude[0] is None and claude[1]["parts"]["cache_read"] == 80


def test_summary_totals_and_streak(conn, settings):
    seed(conn, settings)
    s = views.summary(conn, "2026-09-24", "2026-09-27")
    assert s["tokens"] == 100 + 50 + 70 + 40
    assert s["cost_usd"] == 3.5
    assert s["providers"]["codex"]["cost_coverage"] == pytest.approx(30 / 110)
    assert s["current_streak"] == 3  # 24, 25, 26; the 27th has not started
    assert s["peak_day"] == {"day": "2026-09-25", "tokens": 100}


def test_models_excludes_account_rows(conn, settings):
    seed(conn, settings)
    assert views.models(conn, "2026-09-01", "2026-09-30") == [
        {"provider": "claude", "model": "claude-x", "tokens": 50, "cost_usd": 1.0, "days": 1}
    ]


def test_api_endpoints(tmp_path, settings, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    (data / "settings.json").write_text(
        json.dumps({"timezone": "Europe/London", "grace_days": 3, "machine": {"id": "test-mac", "label": "t", "os": "darwin"}})
    )
    app = create_app(Paths(data=data), static_dir=tmp_path / "nostatic")
    from tokenledger.store import connect

    conn = connect(data / "ledger.sqlite")
    seed(conn, settings)
    conn.close()
    client = TestClient(app)
    assert client.get("/api/status").json()["ledger_rows"] == 6
    s = client.get("/api/summary", params={"start": "2026-09-24", "end": "2026-09-26"}).json()
    assert s["tokens"] == 260
    assert client.get("/api/daily", params={"start": "bad"}).status_code == 400
    assert client.get("/api/daily", params={"start": "2026-09-26", "end": "2026-09-01"}).status_code == 400
    assert len(client.get("/api/models").json()) == 1
    assert client.get("/api/revisions").json() == []


def test_api_survives_parallel_requests(tmp_path, settings):
    from concurrent.futures import ThreadPoolExecutor

    data = tmp_path / "data"
    data.mkdir()
    (data / "settings.json").write_text(
        json.dumps({"timezone": "Europe/London", "grace_days": 3, "machine": {"id": "test-mac", "label": "t", "os": "darwin"}})
    )
    client = TestClient(create_app(Paths(data=data), static_dir=tmp_path / "nostatic"))
    with ThreadPoolExecutor(8) as pool:
        codes = list(pool.map(lambda _: client.get("/api/daily").status_code, range(40)))
    assert codes == [200] * 40


def test_quota_peaks(conn, settings):
    from tokenledger.models import QuotaSample
    from tokenledger.store import repo

    def s(window, at, used, resets):
        return QuotaSample("claude", "a", window, None, at, used, resets, "t")

    repo.insert_quota_samples(conn, [
        s("weekly", "2026-09-20T10:00:00Z", 10, "2026-09-22T07:00:00Z"),
        s("weekly", "2026-09-21T10:00:00Z", 60, "2026-09-22T07:03:00Z"),  # same window, jittered reset
        s("weekly", "2026-09-23T10:00:00Z", 5, "2026-09-29T07:00:00Z"),
        s("weekly", "2026-09-23T11:00:00Z", 0, "2026-09-30T11:00:00Z"),  # idle rolling window
        s("weekly", "2026-09-23T12:00:00Z", 0, "2026-09-30T12:00:00Z"),
        s("session", "2026-09-26T22:30:00Z", 40, None),  # 23:30 London, 26 Sep
        s("session", "2026-09-26T23:30:00Z", 70, None),  # 00:30 London, 27 Sep
    ])
    peaks = views.quota_peaks(conn, "2026-09-01", "2026-09-30", settings.tz)
    assert peaks["weekly"] == [{"provider": "claude", "points": [("2026-09-22T07:00:00Z", 60.0), ("2026-09-29T07:00:00Z", 5.0)]}]
    assert peaks["session"] == [{"provider": "claude", "points": [("2026-09-26", 40.0), ("2026-09-27", 70.0)]}]
