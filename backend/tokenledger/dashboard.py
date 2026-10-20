"""Start, stop and inspect the dashboard server as a detached background process.

The server runs in its own session or process group (not tied to any terminal),
writes its state file while it runs (``api/server.py``) and logs to
``<run>/dashboard.log``, which keeps only the latest run. Everything here finds
the server through that state file and checks that the recorded pid really is a
TokenLedger server before signalling it.

Works on macOS, Linux and Windows. On Windows a graceful stop goes through the
server's loopback-only shutdown path, since there is no SIGTERM to send.
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
from pathlib import Path

import psutil

from .api.server import SHUTDOWN_HEADER, SHUTDOWN_PATH, state_path
from .config import project_root, run_dir

DEFAULT_PORT = 8765
START_TIMEOUT_S = 20.0
STOP_TIMEOUT_S = 10.0
WINDOWS = sys.platform == "win32"
COMMAND = ("serve",)  # the tokenledger subcommand that runs the server


class DashboardError(RuntimeError):
    pass


def log_path() -> Path:
    return run_dir() / "dashboard.log"


def frontend_built() -> bool:
    return (project_root() / "frontend" / "dist" / "index.html").exists()


def is_ours(pid: int) -> bool:
    try:
        cmdline = psutil.Process(pid).cmdline()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, ValueError):
        return False
    return command_matches(cmdline, COMMAND)


def command_matches(cmdline: list[str], command: tuple[str, ...]) -> bool:
    """Whether a process command line runs ``tokenledger <command...>``, either as
    ``python -m tokenledger.cli`` or through the ``tokenledger`` script."""
    for i, part in enumerate(cmdline):
        base = os.path.basename(part).lower()
        if part == "tokenledger.cli" or base in ("tokenledger", "tokenledger.exe", "tokenledger-script.py"):
            return tuple(cmdline[i + 1 : i + 1 + len(command)]) == command
    return False


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
    if not is_ours(int(state.get("pid", 0))):
        path.unlink(missing_ok=True)
        return None
    return state


def _healthy(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/api/status", timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if not WINDOWS:  # on Windows SO_REUSEADDR would let two servers share the port
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


def _detached() -> dict:
    """Popen options that keep the child running after this process and its terminal go away."""
    if WINDOWS:
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
        return {"creationflags": flags}
    return {"start_new_session": True}


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
        "frontend_built": frontend_built(),
        "log": str(log_path()),
    }


def start(port: int = DEFAULT_PORT, open_browser: bool = False) -> dict:
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
            [sys.executable, "-m", "tokenledger.cli", *COMMAND, "--port", str(port)],
            stdin=subprocess.DEVNULL,
            stdout=logfile,
            stderr=subprocess.STDOUT,
            cwd=project_root() / "backend",
            **_detached(),
        )
    url = f"http://127.0.0.1:{port}/"
    deadline = time.monotonic() + START_TIMEOUT_S
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise DashboardError(f"server exited during start (code {proc.returncode}):\n{_log_tail()}")
        if _healthy(url, timeout=1.0) and read_state() is not None:
            if open_browser:
                webbrowser.open(url)
            return {**status(), "already_running": False}
        time.sleep(0.25)
    proc.terminate()
    raise DashboardError(f"server did not answer within {START_TIMEOUT_S:.0f}s:\n{_log_tail()}")


def stop() -> dict:
    """Stop the server gracefully; force it only if it does not exit in time."""
    state = read_state()
    if state is None:
        return {"stopped": False, "was_running": False}
    pid = int(state["pid"])
    if WINDOWS:
        _post_shutdown(state)
    else:
        os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + STOP_TIMEOUT_S
    while time.monotonic() < deadline:
        if not is_ours(pid):
            state_path().unlink(missing_ok=True)
            return {"stopped": True, "was_running": True, "forced": False, "pid": pid}
        time.sleep(0.1)
    try:
        psutil.Process(pid).kill()
    except psutil.NoSuchProcess:
        pass
    state_path().unlink(missing_ok=True)
    return {"stopped": True, "was_running": True, "forced": True, "pid": pid}


def restart(port: int | None = None, open_browser: bool = False) -> dict:
    previous = read_state()
    stop()
    return start(port=port if port is not None else (previous or {}).get("port", DEFAULT_PORT), open_browser=open_browser)


def _post_shutdown(state: dict) -> None:
    request = urllib.request.Request(
        f"http://127.0.0.1:{state['port']}{SHUTDOWN_PATH}",
        method="POST",
        data=b"",
        headers={SHUTDOWN_HEADER.decode(): state.get("shutdown_token", "")},
    )
    try:
        urllib.request.urlopen(request, timeout=5).close()
    except (urllib.error.URLError, OSError):
        pass
