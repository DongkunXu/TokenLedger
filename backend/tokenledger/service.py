"""Start, stop and inspect a TokenLedger server as a detached background process.

Used for the dashboard (``tokenledger dashboard ...``) and the sync hub
(``tokenledger hub ...``). Each server runs in its own session or process group
(not tied to any terminal), writes its state file while it runs
(``api/server.py``) and logs to ``<run>/<name>.log``, which keeps only the
latest run. Everything here finds the server through that state file and checks
that the recorded pid really is that TokenLedger server before signalling it.

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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import psutil

from .api.server import PROBE_HEADER, SHUTDOWN_HEADER, SHUTDOWN_PATH, state_path
from .config import project_root, run_dir

START_TIMEOUT_S = 20.0
STOP_TIMEOUT_S = 10.0
WINDOWS = sys.platform == "win32"


class ServiceError(RuntimeError):
    pass


@dataclass(frozen=True)
class Service:
    name: str  # state and log file name
    command: tuple[str, ...]  # tokenledger subcommand that runs the server, e.g. ("serve",)
    health_path: str  # GET path that answers 200 while the server is up

    def state_path(self) -> Path:
        return state_path(self.name)

    def log_path(self) -> Path:
        return run_dir() / f"{self.name}.log"

    # -- finding the server -------------------------------------------------------------

    def is_ours(self, pid: int) -> bool:
        try:
            cmdline = psutil.Process(pid).cmdline()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, ValueError):
            return False
        return command_matches(cmdline, self.command)

    def read_state(self) -> dict | None:
        """The running server's state, or None. A state file left by a crashed server is removed."""
        path = self.state_path()
        try:
            state = json.loads(path.read_text())
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            path.unlink(missing_ok=True)
            return None
        if not self.is_ours(int(state.get("pid", 0))):
            path.unlink(missing_ok=True)
            return None
        return state

    def healthy(self, base_url: str, timeout: float = 2.0) -> bool:
        # Marked as a probe so that checking on the server does not reset its idle timer.
        request = urllib.request.Request(base_url.rstrip("/") + self.health_path, headers={PROBE_HEADER.decode(): "1"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                return resp.status == 200
        except (urllib.error.URLError, OSError):
            return False

    def log_tail(self, lines: int = 15) -> str:
        try:
            return "\n".join(self.log_path().read_text(errors="replace").splitlines()[-lines:])
        except FileNotFoundError:
            return ""

    # -- lifecycle ----------------------------------------------------------------------

    def status(self) -> dict:
        state = self.read_state()
        if state is None:
            return {"running": False, "log": str(self.log_path())}
        started = datetime.fromisoformat(state["started_at"])
        return {
            "running": True,
            "healthy": self.healthy(probe_url(state)),
            "pid": state["pid"],
            "url": state["url"],
            "started_at": state["started_at"],
            "uptime_minutes": round((datetime.now(timezone.utc) - started).total_seconds() / 60, 1),
            "idle_hours": state["idle_hours"],
            "log": str(self.log_path()),
        }

    def start(self, args: list[str], *, bind_host: str, port: int, open_url: str | None = None) -> dict:
        """Start the server detached with ``args`` after the subcommand. An already running
        server is left as is."""
        existing = self.status()
        if existing["running"]:
            if open_url:
                webbrowser.open(existing["url"])
            return {**existing, "already_running": True}
        if not port_free(bind_host, port):
            raise ServiceError(f"port {port} is already in use by another program")

        run_dir().mkdir(parents=True, exist_ok=True)
        with open(self.log_path(), "w") as logfile:  # keep only the latest run's log
            proc = subprocess.Popen(
                [sys.executable, "-m", "tokenledger.cli", *self.command, *args],
                stdin=subprocess.DEVNULL,
                stdout=logfile,
                stderr=subprocess.STDOUT,
                cwd=project_root() / "backend",
                **detached(),
            )
        probe = f"http://{loopback_for(bind_host)}:{port}/"
        deadline = time.monotonic() + START_TIMEOUT_S
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise ServiceError(f"server exited during start (code {proc.returncode}):\n{self.log_tail()}")
            if self.healthy(probe, timeout=1.0) and self.read_state() is not None:
                if open_url:
                    webbrowser.open(open_url)
                return {**self.status(), "already_running": False}
            time.sleep(0.25)
        proc.terminate()
        raise ServiceError(f"server did not answer within {START_TIMEOUT_S:.0f}s:\n{self.log_tail()}")

    def stop(self) -> dict:
        """Stop the server gracefully; force it only if it does not exit in time."""
        state = self.read_state()
        if state is None:
            return {"stopped": False, "was_running": False}
        pid = int(state["pid"])
        if WINDOWS:
            _post_shutdown(state)
        else:
            os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + STOP_TIMEOUT_S
        while time.monotonic() < deadline:
            if not self.is_ours(pid):
                self.state_path().unlink(missing_ok=True)
                return {"stopped": True, "was_running": True, "forced": False, "pid": pid}
            time.sleep(0.1)
        try:
            psutil.Process(pid).kill()
        except psutil.NoSuchProcess:
            pass
        self.state_path().unlink(missing_ok=True)
        return {"stopped": True, "was_running": True, "forced": True, "pid": pid}


def command_matches(cmdline: list[str], command: tuple[str, ...]) -> bool:
    """Whether a process command line runs ``tokenledger <command...>``, either as
    ``python -m tokenledger.cli`` or through the ``tokenledger`` script."""
    for i, part in enumerate(cmdline):
        base = os.path.basename(part).lower()
        if part == "tokenledger.cli" or base in ("tokenledger", "tokenledger.exe", "tokenledger-script.py"):
            return tuple(cmdline[i + 1 : i + 1 + len(command)]) == command
    return False


def detached() -> dict:
    """Popen options that keep the child running after this process and its terminal go away."""
    if WINDOWS:
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
        return {"creationflags": flags}
    return {"start_new_session": True}


def loopback_for(host: str) -> str:
    return "127.0.0.1" if host in ("0.0.0.0", "", "localhost") else ("::1" if host == "::" else host)


def probe_url(state: dict) -> str:
    return f"http://{loopback_for(state.get('host', '127.0.0.1'))}:{state['port']}/"


def port_free(host: str, port: int) -> bool:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as s:
        if not WINDOWS:  # on Windows SO_REUSEADDR would let two servers share the port
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host or "0.0.0.0", port))
        except OSError:
            return False
    return True


def _post_shutdown(state: dict) -> None:
    request = urllib.request.Request(
        probe_url(state).rstrip("/") + SHUTDOWN_PATH,
        method="POST",
        data=b"",
        headers={SHUTDOWN_HEADER.decode(): state.get("shutdown_token", "")},
    )
    try:
        urllib.request.urlopen(request, timeout=5).close()
    except (urllib.error.URLError, OSError):
        pass
