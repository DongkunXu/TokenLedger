"""The dashboard server process: uvicorn plus a controlled lifecycle.

* writes a state file (pid, port, start time, idle limit) when it starts and removes
  it when it exits, so ``tokenledger dashboard status/stop`` can find it
* shuts down gracefully on Ctrl+C, SIGTERM (``dashboard stop``) and SIGHUP (the terminal
  that started it closed)
* stops by itself after ``idle_hours`` without any HTTP request (0 keeps it running)
* logs one line per lifecycle event to stdout (the log file when started detached)
"""

from __future__ import annotations

import json
import os
import signal
import threading
import time
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

import uvicorn

from ..config import run_dir
from .app import create_app

ALLOWED_HOSTS = ("127.0.0.1", "localhost", "::1")
DEFAULT_IDLE_HOURS = 12.0


def state_path() -> Path:
    return run_dir() / "dashboard.json"


def log(message: str) -> None:
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    print(f"{stamp} {message}", flush=True)


PROBE_HEADER = b"x-tokenledger-probe"


class ActivityTracker:
    """ASGI wrapper that remembers when the last HTTP request arrived (wall clock).

    Health probes from ``tokenledger dashboard`` carry PROBE_HEADER and do not count:
    checking whether the server is up must not keep it alive.
    """

    def __init__(self, app) -> None:
        self.app = app
        self.last = time.time()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and not any(name == PROBE_HEADER for name, _ in scope.get("headers", [])):
            self.last = time.time()
        await self.app(scope, receive, send)


def serve(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = False, idle_hours: float = DEFAULT_IDLE_HOURS) -> None:
    if host not in ALLOWED_HOSTS:
        raise SystemExit("TokenLedger only listens on this machine (127.0.0.1)")
    if idle_hours < 0:
        raise SystemExit("--idle-hours must be >= 0")
    tracker = ActivityTracker(create_app())
    server = uvicorn.Server(uvicorn.Config(tracker, host=host, port=port, log_level="warning"))
    url = f"http://{host}:{port}/"
    reason = {"why": "stopped"}

    def request_exit(why: str) -> None:
        # The first cause wins. For SIGINT/SIGTERM uvicorn sets should_exit itself and then
        # re-raises the signal into these handlers, so the reason is recorded independently.
        if reason["why"] == "stopped":
            reason["why"] = why
        server.should_exit = True

    # uvicorn handles SIGINT/SIGTERM itself and re-raises the signal after shutting down;
    # Python-level handlers here make that re-raise harmless, so the cleanup below runs.
    signal.signal(signal.SIGTERM, lambda *_: request_exit("stopped (SIGTERM)"))
    signal.signal(signal.SIGINT, lambda *_: request_exit("stopped (Ctrl+C)"))
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
    }
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(path)
    limit_text = f"{idle_hours:g} h" if idle_hours > 0 else "off"
    log(f"started pid={state['pid']} url={url} idle-limit={limit_text}")
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        server.run()
    finally:
        try:
            if json.loads(path.read_text()).get("pid") == os.getpid():
                path.unlink()
        except (OSError, ValueError):
            pass
        log(reason["why"])
