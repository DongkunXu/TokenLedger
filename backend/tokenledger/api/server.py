"""Server processes (the dashboard, the sync hub): uvicorn plus a controlled lifecycle.

* writes a state file (pid, port, start time, idle limit) when it starts and removes
  it when it exits, so the management commands (``tokenledger dashboard ...``,
  ``tokenledger hub ...``) can find it
* shuts down gracefully on Ctrl+C, SIGTERM (``... stop``) and SIGHUP (the terminal that
  started it closed). Windows has no SIGTERM for other processes, so ``stop`` there
  posts to a shutdown path with a random token from the state file instead
* stops by itself after ``idle_hours`` without any HTTP request (0 keeps it running)
* logs one line per lifecycle event to stdout (the log file when started detached)
"""

from __future__ import annotations

import ipaddress
import json
import os
import secrets
import signal
import threading
import time
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import uvicorn

from ..config import run_dir

ALLOWED_HOSTS = ("127.0.0.1", "localhost", "::1")
DEFAULT_IDLE_HOURS = 12.0
PROBE_HEADER = b"x-tokenledger-probe"
SHUTDOWN_PATH = "/_tokenledger/shutdown"
SHUTDOWN_HEADER = b"x-tokenledger-shutdown"


def state_path(name: str = "dashboard") -> Path:
    return run_dir() / f"{name}.json"


def log(message: str) -> None:
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    print(f"{stamp} {message}", flush=True)


class ActivityTracker:
    """ASGI wrapper that remembers when the last HTTP request arrived (wall clock).

    Health probes carry PROBE_HEADER and do not count: checking whether the server is
    up must not keep it alive. It also answers the shutdown path (loopback only, with
    the token from the state file).
    """

    def __init__(self, app, *, shutdown_token: str = "", on_shutdown: Callable[[], None] | None = None) -> None:
        self.app = app
        self.last = time.time()
        self.shutdown_token = shutdown_token.encode()
        self.on_shutdown = on_shutdown

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers", []))
            if scope.get("path") == SHUTDOWN_PATH and scope.get("method") == "POST":
                await self._shutdown(scope, headers, send)
                return
            if PROBE_HEADER not in headers:
                self.last = time.time()
        await self.app(scope, receive, send)

    async def _shutdown(self, scope, headers, send) -> None:
        client = (scope.get("client") or ("", 0))[0]
        try:
            local = ipaddress.ip_address(client).is_loopback
        except ValueError:
            local = False
        token = headers.get(SHUTDOWN_HEADER, b"")
        ok = local and bool(self.shutdown_token) and secrets.compare_digest(token, self.shutdown_token)
        await send({"type": "http.response.start", "status": 202 if ok else 403, "headers": []})
        await send({"type": "http.response.body", "body": b""})
        if ok and self.on_shutdown:
            self.on_shutdown()


def run(
    app,
    *,
    name: str,
    host: str,
    port: int,
    url: str,
    idle_hours: float,
    open_browser: bool = False,
    on_exit: Callable[[], None] | None = None,
    extra_state: dict | None = None,
) -> None:
    """Run one server in the foreground until it is told to stop."""
    if idle_hours < 0:
        raise SystemExit("--idle-hours must be >= 0")
    reason = {"why": "stopped"}
    token = secrets.token_hex(16)
    holder: dict = {}

    def request_exit(why: str) -> None:
        # The first cause wins. For SIGINT/SIGTERM uvicorn sets should_exit itself and then
        # re-raises the signal into these handlers, so the reason is recorded independently.
        if reason["why"] == "stopped":
            reason["why"] = why
        holder["server"].should_exit = True

    tracker = ActivityTracker(app, shutdown_token=token, on_shutdown=lambda: request_exit("stopped (shutdown request)"))
    server = uvicorn.Server(uvicorn.Config(tracker, host=host, port=port, log_level="warning"))
    holder["server"] = server

    # uvicorn handles SIGINT/SIGTERM itself and re-raises the signal after shutting down;
    # Python-level handlers here make that re-raise harmless, so the cleanup below runs.
    signal.signal(signal.SIGTERM, lambda *_: request_exit("stopped (SIGTERM)"))
    signal.signal(signal.SIGINT, lambda *_: request_exit("stopped (Ctrl+C)"))
    if hasattr(signal, "SIGHUP"):  # not on Windows
        signal.signal(signal.SIGHUP, lambda *_: request_exit("stopped (terminal closed)"))

    if idle_hours > 0:
        limit = idle_hours * 3600
        interval = min(30.0, max(0.2, limit / 4))

        def watch_idle() -> None:
            while not server.should_exit:
                time.sleep(interval)
                if time.time() - tracker.last >= limit:
                    request_exit(f"stopped after {idle_hours:g} h without requests")

        threading.Thread(target=watch_idle, name="idle-watch", daemon=True).start()

    state = {
        "pid": os.getpid(),
        "host": host,
        "port": port,
        "url": url,
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "idle_hours": idle_hours,
        "shutdown_token": token,
        **(extra_state or {}),
    }
    path = state_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # holds the shutdown token
    with os.fdopen(fd, "w") as fh:
        fh.write(json.dumps(state, indent=2))
    tmp.replace(path)
    limit_text = f"{idle_hours:g} h" if idle_hours > 0 else "off"
    log(f"started pid={state['pid']} url={url} idle-limit={limit_text}")
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        server.run()
    finally:
        if on_exit:
            on_exit()
        try:
            if json.loads(path.read_text()).get("pid") == os.getpid():
                path.unlink()
        except (OSError, ValueError):
            pass
        log(reason["why"])


def serve(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = False, idle_hours: float = DEFAULT_IDLE_HOURS) -> None:
    """The dashboard server. It only ever listens on this machine."""
    from .app import create_app

    if host not in ALLOWED_HOSTS:
        raise SystemExit("TokenLedger only listens on this machine (127.0.0.1)")
    run(
        create_app(),
        name="dashboard",
        host=host,
        port=port,
        url=f"http://{host}:{port}/",
        idle_hours=idle_hours,
        open_browser=open_browser,
    )
