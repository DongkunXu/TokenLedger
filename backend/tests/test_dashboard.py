from __future__ import annotations

import json
import os
import signal
import socket
import time

import pytest

from tokenledger import dashboard


@pytest.fixture
def env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    (data / "settings.json").write_text(
        json.dumps({"timezone": "Europe/London", "grace_days": 3, "machine": {"id": "t", "label": "t", "os": "darwin"}})
    )
    monkeypatch.setenv("TOKENLEDGER_DATA_DIR", str(data))
    monkeypatch.setenv("TOKENLEDGER_RUN_DIR", str(tmp_path / "run"))
    yield
    dashboard.stop()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_until(predicate, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return False


def test_start_status_stop(env):
    port = free_port()
    started = dashboard.start(port=port, idle_hours=0)
    assert started["running"] and started["healthy"] and not started["already_running"]
    assert started["url"] == f"http://127.0.0.1:{port}/"
    again = dashboard.start(port=port, idle_hours=0)
    assert again["already_running"] and again["pid"] == started["pid"]
    stopped = dashboard.stop()
    assert stopped == {"stopped": True, "was_running": True, "forced": False, "pid": started["pid"]}
    assert not dashboard.status()["running"] and not dashboard.state_path().exists()
    log = dashboard.log_path().read_text()
    assert "started pid=" in log and "stopped (SIGTERM)" in log
    assert dashboard.stop() == {"stopped": False, "was_running": False}


def test_stops_itself_when_idle(env):
    dashboard.start(port=free_port(), idle_hours=1.5 / 3600)  # 1.5 seconds
    assert wait_until(lambda: not dashboard.status()["running"])
    assert "without requests" in dashboard.log_path().read_text()


def test_terminal_hangup_is_a_graceful_stop(env):
    pid = dashboard.start(port=free_port(), idle_hours=0)["pid"]
    os.kill(pid, signal.SIGHUP)
    assert wait_until(lambda: not dashboard.status()["running"])
    assert "stopped (terminal closed)" in dashboard.log_path().read_text()
    assert not dashboard.state_path().exists()


def test_stale_state_file_is_ignored(env):
    path = dashboard.state_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"pid": 999999, "url": "http://127.0.0.1:1/", "started_at": "2026-01-01T00:00:00+00:00"}))
    assert dashboard.read_state() is None and not path.exists()


def test_port_taken_by_another_program(env):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        with pytest.raises(dashboard.DashboardError, match="already in use"):
            dashboard.start(port=s.getsockname()[1], idle_hours=0)
