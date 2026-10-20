"""Work the dashboard server does by itself while it runs.

* captures every ``auto_capture_minutes`` (settings.json, 60 by default; 0 turns it off),
  counted from this machine's last capture, whoever started it
* syncs with the cloud ledger every ``SYNC_MINUTES`` while signed in, so this machine merges
  the others' data and the cloud dashboard stays current between captures (a capture also
  syncs)

Settings are read again every round, so a change in settings.json applies without a restart.
A round is skipped while another capture or sync is running; nothing here ever stops the
server.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Callable

from . import capture
from .config import default_paths, load_settings
from .store import connect, migrate
from .sync import client as sync_client
from .sync import config as sync_config

TICK_S = 60.0
SYNC_MINUTES = 10


class BackgroundWork:
    def __init__(self, *, log: Callable[[str], None], tick: float = TICK_S, first_after: float = 5.0) -> None:
        self.log = log
        self.tick = tick
        self.first_after = first_after
        self.last_sync: datetime | None = None
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._run, name="background", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stopped.set()

    def _run(self) -> None:
        wait = self.first_after
        while not self.stopped.wait(wait):
            wait = self.tick
            try:
                self.round(datetime.now(timezone.utc))
            except Exception as exc:  # noqa: BLE001 - logged; the next round tries again
                self.log(f"background: {type(exc).__name__}: {exc}")

    def round(self, now: datetime) -> str | None:
        """One check: capture when due, else sync when due. Returns what it did."""
        paths = default_paths()
        settings = load_settings(paths)
        conn = connect(paths.db)
        try:
            migrate(conn)
            every = settings.auto_capture_minutes
            last = capture.last_own_capture(conn)
            if every and (last is None or now - last >= timedelta(minutes=every)):
                outcomes = capture.run_default(conn, paths, settings)
                if outcomes is None:
                    return None
                failed = [o.source for o in outcomes if not o.ok]
                if failed:
                    self.log(f"auto capture: failed: {', '.join(failed)}")
                self.last_sync = now
                return "capture"
            if sync_config.load(paths) is None:
                return None
            if self.last_sync is not None and now - self.last_sync < timedelta(minutes=SYNC_MINUTES):
                return None
            result = sync_client.sync(conn, paths, settings, wait=False)
            if result.get("busy"):
                return None
            self.last_sync = now
            if not result.get("ok"):
                self.log(f"sync failed: {result.get('error')}")
            return "sync"
        finally:
            conn.close()
