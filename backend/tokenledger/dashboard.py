"""Start, stop and inspect the dashboard server as a detached background process.

A thin layer over ``service.py``: the dashboard listens on 127.0.0.1 only and stops
by itself after ``idle_hours`` without requests.
"""

from __future__ import annotations

from .api.server import DEFAULT_IDLE_HOURS
from .config import project_root
from .service import Service, ServiceError

DEFAULT_PORT = 8765
SERVICE = Service(name="dashboard", command=("serve",), health_path="/api/status")
DashboardError = ServiceError

__all__ = ["DEFAULT_IDLE_HOURS", "DEFAULT_PORT", "DashboardError", "frontend_built", "log_path", "read_state",
           "restart", "start", "state_path", "status", "stop"]


def state_path():
    return SERVICE.state_path()


def log_path():
    return SERVICE.log_path()


def read_state() -> dict | None:
    return SERVICE.read_state()


def frontend_built() -> bool:
    return (project_root() / "frontend" / "dist" / "index.html").exists()


def status() -> dict:
    return {**SERVICE.status(), "frontend_built": frontend_built()}


def start(port: int = DEFAULT_PORT, idle_hours: float = DEFAULT_IDLE_HOURS, open_browser: bool = False) -> dict:
    """Start the server detached. Returns its status; an already running server is left as is."""
    result = SERVICE.start(
        ["--port", str(port), "--idle-hours", str(idle_hours)],
        bind_host="127.0.0.1",
        port=port,
        open_url=f"http://127.0.0.1:{port}/" if open_browser else None,
    )
    return {**result, "frontend_built": frontend_built()}


def stop() -> dict:
    return SERVICE.stop()


def restart(port: int | None = None, idle_hours: float | None = None, open_browser: bool = False) -> dict:
    previous = read_state()
    stop()
    return start(
        port=port if port is not None else (previous or {}).get("port", DEFAULT_PORT),
        idle_hours=idle_hours if idle_hours is not None else (previous or {}).get("idle_hours", DEFAULT_IDLE_HOURS),
        open_browser=open_browser,
    )
