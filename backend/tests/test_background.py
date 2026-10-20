"""What the dashboard server does by itself: capture on a schedule, sync in between."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from tokenledger import background, capture
from tokenledger.store import connect, migrate, repo
from tokenledger.sync import client as sync_client
from tokenledger.sync import config as sync_config

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("TOKENLEDGER_DATA_DIR", str(data))
    calls: list[str] = []
    monkeypatch.setattr(capture, "run_default", lambda conn, paths, settings: calls.append("capture") or [])
    monkeypatch.setattr(sync_client, "sync", lambda conn, paths, settings, wait: calls.append("sync") or {"ok": True})

    def settings(**extra):
        base = {"timezone": "Europe/London", "grace_days": 3, "machine": {"id": "m", "label": "m", "os": "darwin"}}
        (data / "settings.json").write_text(json.dumps({**base, **extra}))

    def captured_at(at: datetime):
        conn = connect(data / "ledger.sqlite")
        migrate(conn)
        with repo.transaction(conn):
            cid = repo.start_capture(conn, "usage", "codexbar", None, at.isoformat())
            repo.finish_capture(conn, cid, at.isoformat(), status="ok")
        conn.close()

    def signed_in():
        sync_config.save(_paths(data), sync_config.Link("https://ledger.example", "me", "d", "t", "m", NOW.isoformat()))

    return settings, captured_at, signed_in, calls


def _paths(data):
    from tokenledger.config import Paths

    return Paths(data=data)


def test_captures_when_due_and_syncs_in_between(env):
    settings, captured_at, signed_in, calls = env
    settings()
    work = background.BackgroundWork(log=lambda line: None)
    assert work.round(NOW) == "capture" and calls == ["capture"]  # never captured: right away
    captured_at(NOW - timedelta(minutes=30))
    assert work.round(NOW) is None  # captured half an hour ago, not signed in: nothing to do
    signed_in()
    work.last_sync = None
    assert work.round(NOW) == "sync" and calls == ["capture", "sync"]
    assert work.round(NOW + timedelta(minutes=5)) is None  # synced five minutes ago
    assert work.round(NOW + timedelta(minutes=31)) == "capture"  # an hour after the last capture


def test_auto_capture_can_be_turned_off(env):
    settings, captured_at, signed_in, calls = env
    settings(auto_capture_minutes=0)
    assert background.BackgroundWork(log=lambda line: None).round(NOW) is None and calls == []
    settings(auto_capture_minutes=5)
    with pytest.raises(ValueError, match="at least 10"):
        background.BackgroundWork(log=lambda line: None).round(NOW)
