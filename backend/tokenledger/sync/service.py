"""The hub as a background service: ``tokenledger hub start|stop|restart|status``.

It never stops by itself (no idle limit); to keep it running across restarts,
install it as a login service (``scripts/install-hub-agent.sh`` on macOS and
Linux, ``scripts/install-hub-task.ps1`` on Windows).
"""

from __future__ import annotations

from ..config import default_paths
from ..service import Service
from .hub import HELLO_PATH, hub_config

HUB = Service(name="hub", command=("hub", "serve"), health_path=HELLO_PATH)


def start_hub(*, host: str | None = None, port: int | None = None) -> dict:
    cfg = hub_config(default_paths())
    host = host or cfg.listen_host
    port = port or cfg.port
    return HUB.start(["--host", host, "--port", str(port)], bind_host=host, port=port)
