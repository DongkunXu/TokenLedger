"""The dashboard server process: uvicorn plus a controlled lifecycle.

* writes a state file (pid, port, start time) when it starts and removes it when it
  exits, so ``tokenledger dashboard status/stop`` can find it
* runs until it is told to stop: Ctrl+C, SIGTERM (``dashboard stop``) or SIGHUP (the
  terminal that started it closed). Windows has no SIGTERM for other processes, so
  ``stop`` there posts to a shutdown path with a random token from the state file
* while this machine is signed in to a cloud ledger, syncs with it in the background
  (``sync/client.py``), so the dashboard shows the other machines' latest data
* logs one line per lifecycle event to stdout (the log file when started detached)
"""

from __future__ import annotations

import ipaddress
import json
import os
import secrets
import signal
import threading
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import uvicorn

from ..config import run_dir

ALLOWED_HOSTS = ("127.0.0.1", "localhost", "::1")
SHUTDOWN_PATH = "/_tokenledger/shutdown"
SHUTDOWN_HEADER = b"x-tokenledger-shutdown"


def state_path(name: str = "dashboard") -> Path:
    return run_dir() / f"{name}.json"


def log(message: str) -> None:
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    print(f"{stamp} {message}", flush=True)


class LocalOnly:
    """ASGI wrapper for a server that only this machine may use.

    * refuses requests whose Host header is not a loopback name, so a web page cannot
      reach the dashboard's API by pointing its own domain at 127.0.0.1 (DNS rebinding),
      and changing requests sent from another page's origin
    * answers the shutdown path (loopback clients with the token from the state file)
    """

    def __init__(self, app, *, port: int, shutdown_token: str, on_shutdown: Callable[[], None]) -> None:
        self.app = app
        self.hosts = {f"{h}:{port}".encode() for h in ("127.0.0.1", "localhost", "[::1]")}
        self.origins = {b"http://" + h for h in self.hosts}
        self.shutdown_token = shutdown_token.encode()
        self.on_shutdown = on_shutdown

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            headers = dict(scope.get("headers", []))
            if headers.get(b"host") not in self.hosts:
                await _respond(send, 421)
                return
            # Another web page may send simple requests here; only this dashboard may change things.
            origin = headers.get(b"origin")
            if scope.get("method") not in ("GET", "HEAD") and origin is not None and origin not in self.origins:
                await _respond(send, 403)
                return
            if scope.get("path") == SHUTDOWN_PATH and scope.get("method") == "POST":
                ok = _loopback(scope) and secrets.compare_digest(headers.get(SHUTDOWN_HEADER, b""), self.shutdown_token)
                await _respond(send, 202 if ok else 403)
                if ok:
                    self.on_shutdown()
                return
        await self.app(scope, receive, send)


def _loopback(scope) -> bool:
    try:
        return ipaddress.ip_address((scope.get("client") or ("", 0))[0]).is_loopback
    except ValueError:
        return False


async def _respond(send, status: int) -> None:
    await send({"type": "http.response.start", "status": status, "headers": []})
    await send({"type": "http.response.body", "body": b""})


def serve(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = False) -> None:
    """Run the dashboard server in the foreground until it is told to stop."""
    from .app import create_app
    from ..sync.client import BackgroundSync

    if host not in ALLOWED_HOSTS:
        raise SystemExit("TokenLedger only listens on this machine (127.0.0.1)")
    url = f"http://{host}:{port}/"
    reason = {"why": "stopped"}
    token = secrets.token_hex(16)
    holder: dict = {}

    def request_exit(why: str) -> None:
        # The first cause wins. For SIGINT/SIGTERM uvicorn sets should_exit itself and then
        # re-raises the signal into these handlers, so the reason is recorded independently.
        if reason["why"] == "stopped":
            reason["why"] = why
        holder["server"].should_exit = True

    app = LocalOnly(create_app(), port=port, shutdown_token=token, on_shutdown=lambda: request_exit("stopped (shutdown request)"))
    # The dashboard is reached directly, never through a proxy: X-Forwarded-For is ignored.
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning", proxy_headers=False))
    holder["server"] = server

    # uvicorn handles SIGINT/SIGTERM itself and re-raises the signal after shutting down;
    # Python-level handlers here make that re-raise harmless, so the cleanup below runs.
    signal.signal(signal.SIGTERM, lambda *_: request_exit("stopped (SIGTERM)"))
    signal.signal(signal.SIGINT, lambda *_: request_exit("stopped (Ctrl+C)"))
    if hasattr(signal, "SIGHUP"):  # not on Windows
        signal.signal(signal.SIGHUP, lambda *_: request_exit("stopped (terminal closed)"))

    state = {
        "pid": os.getpid(),
        "host": host,
        "port": port,
        "url": url,
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "shutdown_token": token,
    }
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # holds the shutdown token
    with os.fdopen(fd, "w") as fh:
        fh.write(json.dumps(state, indent=2))
    tmp.replace(path)
    log(f"started pid={state['pid']} url={url}")
    background = BackgroundSync(log=log)
    background.start()
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        server.run()
    finally:
        background.stop()
        try:
            if json.loads(path.read_text()).get("pid") == os.getpid():
                path.unlink()
        except (OSError, ValueError):
            pass
        log(reason["why"])
