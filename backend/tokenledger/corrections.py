"""Deliberate, recorded changes to final rows.

The ledger never changes a final row by itself. When a revision shows that the
source has corrected itself, ``accept_revision`` adopts the source's value: it
re-reads that capture's raw archive to rebuild the complete row (all token
fields, not only the total), writes it, and keeps the old value as a
``replaced`` revision. Revisions that report a missing day cannot be accepted;
the ledger does not delete history.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from . import ledger, rawstore
from .config import Paths, Settings
from .models import DayUsage
from .sources import SourceContext
from .sources.codex_server import parse_usage_response
from .sources.codexbar import parse_cost_export
from .store import repo


def _rows_from_capture(paths: Paths, settings: Settings, capture: sqlite3.Row, provider: str, now: datetime) -> list[DayUsage]:
    raw = rawstore.read(paths.data / capture["raw_path"])
    if capture["source"] == "codexbar":
        result = parse_cost_export(raw, provider=provider, ctx=SourceContext(settings, now), tool_version="archived")
        return result.rows if result else []
    if capture["source"] == "codex-server":
        return parse_usage_response(json.loads(raw), raw=raw, tool_version="archived").rows
    raise ValueError(f"no parser for source {capture['source']!r}")


def accept_revision(
    conn: sqlite3.Connection, paths: Paths, settings: Settings, revision_id: int, *, now_utc: datetime
) -> dict:
    rev = conn.execute("SELECT * FROM revisions WHERE id=?", (revision_id,)).fetchone()
    if rev is None:
        raise ValueError(f"revision {revision_id} not found")
    if rev["kind"] != "changed":
        raise ValueError(f"revision {revision_id} is '{rev['kind']}'; only 'changed' revisions can be accepted")
    capture = conn.execute("SELECT * FROM captures WHERE id=?", (rev["capture_id"],)).fetchone()
    if capture is None or not capture["raw_path"]:
        raise ValueError(f"revision {revision_id} has no archived capture to rebuild the row from")
    key = repo.series_key(conn, rev["series_id"])
    rows = _rows_from_capture(paths, settings, capture, key.provider, now_utc)
    match = [r for r in rows if r.day == rev["day"] and r.model == rev["model"]]
    if len(match) != 1:
        raise ValueError(f"capture {capture['id']} does not contain {rev['day']}/{rev['model']}")
    usage = match[0]
    if usage.total != rev["observed_total"]:
        raise ValueError("archived capture disagrees with the revision; refusing to guess")

    with repo.transaction(conn):
        cid = repo.start_capture(conn, "backfill", "manual", rev["series_id"], ledger.iso(now_utc))
        stats = ledger.apply_backfill(
            conn, key, [usage], settings=settings, now_utc=now_utc, capture_id=cid,
            origin=f"manual:revision:{revision_id}",
        )
        repo.finish_capture(conn, cid, ledger.iso(now_utc), status="ok", tool_version="manual", stats=stats.as_dict())
    return {"revision": revision_id, "day": rev["day"], "model": rev["model"], "new_total": usage.total, **stats.as_dict()}
