"""Start, stop and inspect the dashboard server as a detached background process.

The server runs in its own session (not tied to any terminal), writes its state file
while it runs (``api/server.py``) and logs to ``<run>/dashboard.log``, which keeps only
the latest run. Everything here finds the server through that state file and checks
that the recorded pid really is a TokenLedger server before signalling it.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime, timezone

from .api.server import DEFAULT_IDLE_HOURS, PROBE_HEADER, state_path
from .config import project_root, run_dir

DEFAULT_PORT = 8765
START_TIMEOUT_S = 20.0
STOP_TIMEOUT_S = 10.0


class DashboardError(RuntimeError):
    pass


def log_path():
    return run_dir() / "dashboard.log"


def _is_our_server(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return False
    out = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True)
    command = out.stdout.strip()
    return "tokenledger" in command and " serve" in command


def read_state() -> dict | None:
    """The running server's state, or None. A state file left by a crashed server is removed."""
    path = state_path()
    try:
        state = json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        path.unlink(missing_ok=True)
        return None
    if not _is_our_server(int(state.get("pid", 0))):
        path.unlink(missing_ok=True)
        return None
    return state


def _healthy(url: str, timeout: float = 2.0) -> bool:
    # Marked as a probe so that checking on the server does not reset its idle timer.
    request = urllib.request.Request(url + "api/status", headers={PROBE_HEADER.decode(): "1"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _log_tail(lines: int = 15) -> str:
    try:
        return "\n".join(log_path().read_text(errors="replace").splitlines()[-lines:])
    except FileNotFoundError:
        return ""


def frontend_built() -> bool:
    return (project_root() / "frontend" / "dist" / "index.html").exists()


def status() -> dict:
    state = read_state()
    if state is None:
        return {"running": False, "frontend_built": frontend_built(), "log": str(log_path())}
    started = datetime.fromisoformat(state["started_at"])
    return {
        "running": True,
        "healthy": _healthy(state["url"]),
        "pid": state["pid"],
        "url": state["url"],
        "started_at": state["started_at"],
        "uptime_minutes": round((datetime.now(timezone.utc) - started).total_seconds() / 60, 1),
        "idle_hours": state["idle_hours"],
        "frontend_built": frontend_built(),
        "log": str(log_path()),
    }


def start(port: int = DEFAULT_PORT, idle_hours: float = DEFAULT_IDLE_HOURS, open_browser: bool = False) -> dict:
    """Start the server detached. Returns its status; an already running server is left as is."""
    existing = status()
    if existing["running"]:
        if open_browser:
            webbrowser.open(existing["url"])
        return {**existing, "already_running": True}
    if not _port_free(port):
        raise DashboardError(f"port {port} is already in use by another program")

    run_dir().mkdir(parents=True, exist_ok=True)
    with open(log_path(), "w") as logfile:  # keep only the latest run's log
        proc = subprocess.Popen(
            [sys.executable, "-m", "tokenledger.cli", "serve", "--port", str(port), "--idle-hours", str(idle_hours)],
            stdin=subprocess.DEVNULL,
            stdout=logfile,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # own session: closing a terminal does not hang it up
            cwd=project_root() / "backend",
        )
    url = f"http://127.0.0.1:{port}/"
    deadline = time.monotonic() + START_TIMEOUT_S
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise DashboardError(f"server exited during start (code {proc.returncode}):\n{_log_tail()}")
        if _healthy(url, timeout=1.0):
            if open_browser:
                webbrowser.open(url)
            return {**status(), "already_running": False}
        time.sleep(0.25)
    proc.terminate()
    raise DashboardError(f"server did not answer within {START_TIMEOUT_S:.0f}s:\n{_log_tail()}")


def stop() -> dict:
    """Stop the server gracefully (SIGTERM); force it only if it does not exit in time."""
    state = read_state()
    if state is None:
        return {"stopped": False, "was_running": False}
    pid = int(state["pid"])
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + STOP_TIMEOUT_S
    while time.monotonic() < deadline:
        if not _is_our_server(pid):
            state_path().unlink(missing_ok=True)
            return {"stopped": True, "was_running": True, "forced": False, "pid": pid}
        time.sleep(0.1)
    os.kill(pid, signal.SIGKILL)
    state_path().unlink(missing_ok=True)
    return {"stopped": True, "was_running": True, "forced": True, "pid": pid}


def restart(port: int | None = None, idle_hours: float | None = None, open_browser: bool = False) -> dict:
    previous = read_state()
    stop()
    return start(
        port=port if port is not None else (previous or {}).get("port", DEFAULT_PORT),
        idle_hours=idle_hours if idle_hours is not None else (previous or {}).get("idle_hours", DEFAULT_IDLE_HOURS),
        open_browser=open_browser,
    )
