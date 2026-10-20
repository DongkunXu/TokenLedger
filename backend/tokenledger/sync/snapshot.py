"""What a machine sends to the hub, and how the hub merges it.

Each push carries the machine's whole ledger state that it is responsible for,
not a list of changes: every row of its own series (``scope = machine`` and its
machine id), every account-wide row it has read, their revisions, its latest
captures (for source health) and its quota samples. A ledger is small (a few
thousand rows a year), and sending everything each time means nothing can be
skipped by a lost or reordered message: the next push repairs any gap. Applying
the same snapshot twice changes nothing.

On the hub:

* rows of a machine's own series are mirrored: that machine is the only one
  that writes them, and its own ledger has already applied the settling rules.
  Rows are added or updated, never deleted, the same as everywhere in the ledger
* account-wide rows (for example the Codex server's daily totals) can come from
  several machines signed in to the same account. A final row stays as it is;
  an open row takes the incoming row when that one is final or newer
* revisions and captures are added once, keyed by the sender's own ids
* a snapshot older than the last one applied from that machine is ignored, so a
  delayed request can never roll the hub back
* a snapshot bucketed in another timezone is refused: its days would not line up
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from ..config import Settings
from ..models import CostQuality, DayUsage, Scope, SeriesKey, State
from ..store import repo

FORMAT = "tokenledger-snapshot/1"
ROW_COLUMNS = (
    "day",
    "model",
    "total",
    "input_uncached",
    "cache_read",
    "cache_write",
    "output",
    "reasoning",
    "cost_micros",
    "cost_quality",
    "state",
    "origin",
    "first_seen_at",
    "updated_at",
    "finalized_at",
)
REVISION_COLUMNS = (
    "id",
    "day",
    "model",
    "kind",
    "observed_at",
    "origin",
    "observed_total",
    "observed_cost",
    "ledger_total",
    "ledger_cost",
)
CAPTURE_COLUMNS = ("id", "kind", "source", "started_at", "finished_at", "status", "tool_version", "stats", "error")
QUOTA_COLUMNS = ("provider", "account", "window", "captured_at", "window_minutes", "used_percent", "resets_at", "source")
CAPTURES_SENT = 300  # the newest captures; enough for source health and recent history


class SyncRefused(ValueError):
    """The hub will not take this snapshot; the message says why."""


def snapshot_time() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def build(conn: sqlite3.Connection, settings: Settings, *, app_version: str, taken_at: str | None = None) -> dict:
    """Everything this machine is responsible for, as one snapshot."""
    me = settings.machine
    series_rows = conn.execute(
        "SELECT * FROM series WHERE (scope = 'machine' AND machine = ?) OR scope = 'account' ORDER BY id", (me.id,)
    ).fetchall()
    series = []
    index: dict[int, int] = {}
    for s in series_rows:
        rows = conn.execute(
            f"SELECT {', '.join(ROW_COLUMNS)} FROM ledger WHERE series_id = ? ORDER BY day, model", (s["id"],)
        ).fetchall()
        revisions = conn.execute(
            f"SELECT {', '.join(REVISION_COLUMNS)} FROM revisions WHERE series_id = ? AND sync_ref IS NULL ORDER BY id",
            (s["id"],),
        ).fetchall()
        index[s["id"]] = len(series)
        series.append(
            {
                "key": [s["source"], s["provider"], s["machine"], s["scope"]],
                "rows": [list(r) for r in rows],
                "revisions": [list(r) for r in revisions],
            }
        )
    captures = conn.execute(
        f"SELECT series_id, {', '.join(CAPTURE_COLUMNS)} FROM captures "
        "WHERE machine IS NULL AND status != 'running' ORDER BY id DESC LIMIT ?",
        (CAPTURES_SENT,),
    ).fetchall()
    quota = conn.execute(f"SELECT {', '.join(QUOTA_COLUMNS)} FROM quota_samples ORDER BY captured_at").fetchall()
    return {
        "format": FORMAT,
        "machine": {"id": me.id, "label": me.label, "os": me.os},
        "app_version": app_version,
        "timezone": settings.timezone,
        "taken_at": taken_at or snapshot_time(),
        "columns": {"rows": ROW_COLUMNS, "revisions": REVISION_COLUMNS, "captures": CAPTURE_COLUMNS, "quota": QUOTA_COLUMNS},
        "series": series,
        "captures": [[index.get(c["series_id"]), *list(c)[1:]] for c in captures],
        "quota": [list(q) for q in quota],
    }


def apply(conn: sqlite3.Connection, snapshot: dict, *, sender: str, settings: Settings, now: str) -> dict:
    """Merge one machine's snapshot into this (the hub's) ledger. Call inside a transaction."""
    _check(snapshot, sender=sender, settings=settings)
    machine = snapshot["machine"]
    held = conn.execute("SELECT last_snapshot_at FROM machines WHERE id = ?", (sender,)).fetchone()
    if held and held["last_snapshot_at"] and snapshot["taken_at"] <= held["last_snapshot_at"]:
        return {"stale": True, "applied_snapshot": held["last_snapshot_at"]}

    stats = {"inserted": 0, "updated": 0, "unchanged": 0, "kept_final": 0, "revisions": 0, "captures": 0, "quota": 0}
    sids: list[int] = []
    for entry in snapshot["series"]:
        key = _series_key(entry["key"], sender=sender, hub=settings.machine.id)
        sid = repo.series_id(conn, key)
        sids.append(sid)
        for values in entry["rows"]:
            row = _row(values)
            if key.scope == Scope.MACHINE:
                _mirror(conn, sid, row, sender, stats)
            else:
                _merge_account(conn, sid, row, sender, stats)
        for values in entry["revisions"]:
            stats["revisions"] += _add_revision(conn, sid, dict(zip(REVISION_COLUMNS, values)), sender)

    for values in snapshot["captures"]:
        stats["captures"] += _add_capture(conn, sids, values, sender)
    before = conn.total_changes
    conn.executemany(
        f"INSERT OR IGNORE INTO quota_samples({', '.join(QUOTA_COLUMNS)}) VALUES({', '.join('?' * len(QUOTA_COLUMNS))})",
        [tuple(q) for q in snapshot["quota"]],
    )
    stats["quota"] = conn.total_changes - before

    conn.execute(
        "INSERT INTO machines(id, label, os, first_seen_at, app_version, last_push_at, last_snapshot_at, last_push_stats) "
        "VALUES(?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET label=excluded.label, os=excluded.os, "
        "app_version=excluded.app_version, last_push_at=excluded.last_push_at, "
        "last_snapshot_at=excluded.last_snapshot_at, last_push_stats=excluded.last_push_stats",
        (sender, machine["label"], machine["os"], now, snapshot.get("app_version"), now, snapshot["taken_at"], json.dumps(stats)),
    )
    return {"stale": False, **stats}


def _check(snapshot: dict, *, sender: str, settings: Settings) -> None:
    if snapshot.get("format") != FORMAT:
        raise SyncRefused(f"unknown snapshot format {snapshot.get('format')!r}; update TokenLedger on both machines")
    if snapshot["machine"]["id"] != sender:
        raise SyncRefused("snapshot names a different machine than the request")
    if sender == settings.machine.id:
        raise SyncRefused(
            "this machine has the hub's machine id; was the hub's data folder copied here? "
            "Give it its own id by removing data/settings.json before its first capture"
        )
    if snapshot["timezone"] != settings.timezone:
        raise SyncRefused(
            f"this machine buckets days in {snapshot['timezone']}, the hub in {settings.timezone}; "
            "set the same timezone in data/settings.json on both"
        )
    if tuple(snapshot["columns"]["rows"]) != ROW_COLUMNS or tuple(snapshot["columns"]["quota"]) != QUOTA_COLUMNS:
        raise SyncRefused("snapshot columns differ from this version; update TokenLedger on both machines")


def _series_key(key: list, *, sender: str, hub: str) -> SeriesKey:
    source, provider, machine, scope = key
    scope = Scope(scope)
    if scope == Scope.MACHINE and machine != sender:
        raise SyncRefused(f"a machine may only send its own series, not {machine!r}")
    if machine == hub:
        raise SyncRefused("a snapshot may not write the hub's own series")
    return SeriesKey(source, provider, machine, scope)


def _row(values: list) -> dict:
    row = dict(zip(ROW_COLUMNS, values))
    if row["state"] not in (State.OPEN, State.FINAL):
        raise SyncRefused(f"bad row state {row['state']!r}")
    # Building the usage checks totals and that cost matches its quality.
    try:
        usage = DayUsage(
            day=row["day"],
            model=row["model"],
            total=row["total"],
            input_uncached=row["input_uncached"],
            cache_read=row["cache_read"],
            cache_write=row["cache_write"],
            output=row["output"],
            reasoning=row["reasoning"],
            cost_micros=row["cost_micros"],
            cost_quality=CostQuality(row["cost_quality"]),
        )
    except ValueError as exc:
        raise SyncRefused(f"bad row {row['day']}/{row['model']}: {exc}") from exc
    row["usage"] = usage
    return row


def _upsert(conn: sqlite3.Connection, sid: int, row: dict, sender: str, existing: sqlite3.Row | None) -> None:
    origin = row["origin"] if row["origin"].startswith("sync:") else f"sync:{sender}:{row['origin']}"
    values = (
        *row["usage"].values(),
        row["state"],
        origin,
        row["first_seen_at"],
        row["updated_at"],
        row["finalized_at"],
    )
    if existing is None:
        conn.execute(
            "INSERT INTO ledger(total, input_uncached, cache_read, cache_write, output, reasoning, cost_micros, "
            "cost_quality, state, origin, first_seen_at, updated_at, finalized_at, series_id, day, model) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (*values, sid, row["day"], row["model"]),
        )
    else:
        conn.execute(
            "UPDATE ledger SET total=?, input_uncached=?, cache_read=?, cache_write=?, output=?, reasoning=?, "
            "cost_micros=?, cost_quality=?, state=?, origin=?, first_seen_at=?, updated_at=?, finalized_at=? "
            "WHERE series_id=? AND day=? AND model=?",
            (*values, sid, row["day"], row["model"]),
        )


def _same(existing: sqlite3.Row, row: dict) -> bool:
    return repo.row_values(existing) == row["usage"].values() and existing["state"] == row["state"]


def _mirror(conn: sqlite3.Connection, sid: int, row: dict, sender: str, stats: dict) -> None:
    existing = repo.ledger_row(conn, sid, row["day"], row["model"])
    if existing is not None and _same(existing, row):
        stats["unchanged"] += 1
        return
    _upsert(conn, sid, row, sender, existing)
    stats["inserted" if existing is None else "updated"] += 1


def _merge_account(conn: sqlite3.Connection, sid: int, row: dict, sender: str, stats: dict) -> None:
    existing = repo.ledger_row(conn, sid, row["day"], row["model"])
    if existing is not None and _same(existing, row):
        stats["unchanged"] += 1
        return
    if existing is not None and existing["state"] == State.FINAL:
        stats["kept_final"] += 1
        return
    if existing is not None and row["state"] == State.OPEN and row["updated_at"] <= existing["updated_at"]:
        stats["unchanged"] += 1  # an older reading of the same account
        return
    _upsert(conn, sid, row, sender, existing)
    stats["inserted" if existing is None else "updated"] += 1


def _add_revision(conn: sqlite3.Connection, sid: int, rev: dict, sender: str) -> int:
    before = conn.total_changes
    conn.execute(
        "INSERT OR IGNORE INTO revisions(series_id, day, model, kind, observed_at, capture_id, origin, observed_total, "
        "observed_cost, ledger_total, ledger_cost, sync_ref) VALUES(?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)",
        (
            sid,
            rev["day"],
            rev["model"],
            rev["kind"],
            rev["observed_at"],
            f"sync:{sender}:{rev['origin']}",
            rev["observed_total"],
            rev["observed_cost"],
            rev["ledger_total"],
            rev["ledger_cost"],
            f"{sender}:{rev['id']}",
        ),
    )
    return conn.total_changes - before


def _add_capture(conn: sqlite3.Connection, sids: list[int], values: list, sender: str) -> int:
    series_index, *rest = values
    cap = dict(zip(CAPTURE_COLUMNS, rest))
    if cap["kind"] not in ("usage", "quota", "backfill") or cap["status"] not in ("ok", "error"):
        return 0
    sid = sids[series_index] if isinstance(series_index, int) and 0 <= series_index < len(sids) else None
    before = conn.total_changes
    conn.execute(
        "INSERT OR IGNORE INTO captures(kind, source, series_id, started_at, finished_at, status, tool_version, stats, "
        "error, machine, sync_ref) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            cap["kind"],
            cap["source"],
            sid,
            cap["started_at"],
            cap["finished_at"],
            cap["status"],
            cap["tool_version"],
            cap["stats"],
            cap["error"],
            sender,
            f"{sender}:{cap['id']}",
        ),
    )
    return conn.total_changes - before
