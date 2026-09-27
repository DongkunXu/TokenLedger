"""Run the dashboard server with uvicorn."""

from __future__ import annotations

import threading
import webbrowser

import uvicorn

from .app import create_app


def serve(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = False) -> None:
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit("TokenLedger only listens on this machine (127.0.0.1)")
    app = create_app()
    url = f"http://{host}:{port}/"
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    print(f"TokenLedger dashboard: {url}")
    uvicorn.run(app, host=host, port=port, log_level="warning")
