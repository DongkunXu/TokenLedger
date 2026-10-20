"""What a machine shares with the others, and how it merges what they share.

A snapshot is everything one machine is responsible for, not a list of changes:
every row of its own series (``scope = machine`` and its machine id), every row of
the account-wide series it reads itself, their revisions, its latest captures (for
source health) and its own quota samples. A ledger is small (a few thousand rows a
year), and sending everything each time means nothing can be skipped by a lost or
reordered upload: the next one repairs any gap. Applying the same snapshot twice
changes nothing.

Merging another machine's snapshot (``apply``):

* rows of its own series are mirrored as that machine has them; it is the only one
  that writes them and has already applied the settling rules. A row that is final
  here keeps its value: a different value from the owner (say its ledger was restored
  from an older backup) is recorded as a revision, unless it is a deliberate
  correction there (``accept`` or a backfill), which is taken over
* account-wide rows (the Codex server's daily totals) can come from several machines
  signed in to the same account. A final row keeps its value, a different final value
  is recorded as a revision; an open row takes an incoming row that is final or newer
* revisions, captures and quota samples are added once, keyed by the sender's own ids
* a snapshot older than the last one applied from that machine is ignored
* a snapshot bucketed in another timezone or settled after another grace period is
  refused: its days would not line up

Restoring this machine's own snapshot (``restore``) is for a ledger that is behind
what this machine shared earlier, for example after its data folder was restored
from a backup or recreated: settled days come back as they were settled then.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

from ..config import Settings
from ..models import CostQuality, DayUsage, Scope, SeriesKey, State
from ..sources.codex_server import LEGACY_ACCOUNT
from ..store import repo

FORMAT = "tokenledger-snapshot/2"
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
QUOTA_DAYS_SENT = 400  # quota history older than this stays where it was sampled
CORRECTION_ORIGINS = ("backfill:", "manual:")  # deliberate changes to a final row


class SyncRefused(ValueError):
    """A snapshot that cannot be merged; the message says why."""


def snapshot_time() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def instance(conn: sqlite3.Connection) -> str:
    value = repo.meta_get(conn, "instance")
    if not value:
        raise RuntimeError("ledger has no instance id; run the migrations first")
    return value


def build(conn: sqlite3.Connection, settings: Settings, *, app_version: str, taken_at: str | None = None) -> dict:
    """Everything this machine is responsible for, as one snapshot."""
    me = settings.machine
    accounts = repo.accounts_read(conn, LEGACY_ACCOUNT)
    series_rows = [
        s
        for s in conn.execute("SELECT * FROM series ORDER BY id").fetchall()
        if (s["scope"] == "machine" and s["machine"] == me.id)
        or (s["scope"] == "account" and [s["provider"], s["machine"]] in accounts)
    ]
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
    since = (datetime.now(timezone.utc) - timedelta(days=QUOTA_DAYS_SENT)).date().isoformat()
    quota = conn.execute(
        f"SELECT {', '.join(QUOTA_COLUMNS)} FROM quota_samples WHERE machine IS NULL AND captured_at >= ? "
        "ORDER BY captured_at",
        (since,),
    ).fetchall()
    return {
        "format": FORMAT,
        "machine": {"id": me.id, "label": me.label, "os": me.os},
        "instance": instance(conn),
        "app_version": app_version,
        "timezone": settings.timezone,
        "grace_days": settings.grace_days,
        "taken_at": taken_at or snapshot_time(),
        "accounts": accounts,
        "account_folds": repo.account_folds(conn),
        "columns": {"rows": ROW_COLUMNS, "revisions": REVISION_COLUMNS, "captures": CAPTURE_COLUMNS, "quota": QUOTA_COLUMNS},
        "series": series,
        "captures": [[index.get(c["series_id"]), *list(c)[1:]] for c in captures],
        "quota": [list(q) for q in quota],
    }


# -- another machine's snapshot -----------------------------------------------------------


def apply(conn: sqlite3.Connection, snapshot: dict, *, sender: str, settings: Settings, now: str) -> dict:
    """Merge another machine's snapshot into this ledger. Call inside a transaction."""
    check(snapshot, settings=settings)
    if snapshot["machine"]["id"] != sender:
        raise SyncRefused(f"snapshot names machine {snapshot['machine']['id']!r}, but was shared as {sender!r}")
    if sender == settings.machine.id:
        raise SyncRefused("snapshot is this machine's own; use restore")
    held = conn.execute("SELECT snapshot_at FROM machines WHERE id = ?", (sender,)).fetchone()
    if held and held["snapshot_at"] and snapshot["taken_at"] <= held["snapshot_at"]:
        return {"stale": True, "applied_snapshot": held["snapshot_at"]}

    ref = f"{sender}:{snapshot['instance']}"
    stats = {"inserted": 0, "updated": 0, "unchanged": 0, "kept_final": 0, "revisions": 0, "captures": 0, "quota": 0}
    for source, provider, old, new in snapshot["account_folds"]:
        # The sender found that two account names are one account; fold this ledger's copy too.
        sid = repo.find_series(conn, SeriesKey(source, provider, old, Scope.ACCOUNT))
        if sid is not None and old not in (new, LEGACY_ACCOUNT, settings.machine.id):
            repo.merge_series(conn, sid, repo.series_id(conn, SeriesKey(source, provider, new, Scope.ACCOUNT)), now)
            repo.add_account_fold(conn, source, provider, old, new)
    sids: list[int] = []
    for entry in snapshot["series"]:
        key = _series_key(entry["key"], sender=sender, me=settings.machine.id)
        sid = repo.series_id(conn, key)
        sids.append(sid)
        for values in entry["rows"]:
            row = _row(values)
            if key.scope == Scope.MACHINE:
                _mirror(conn, sid, row, sender, now, stats)
            else:
                _merge_account(conn, sid, row, sender, now, stats)
        for values in entry["revisions"]:
            stats["revisions"] += _add_revision(conn, sid, dict(zip(REVISION_COLUMNS, values)), sender, ref)

    for values in snapshot["captures"]:
        stats["captures"] += _add_capture(conn, sids, values, sender, ref)
    before = conn.total_changes
    conn.executemany(
        f"INSERT OR IGNORE INTO quota_samples({', '.join(QUOTA_COLUMNS)}, machine) "
        f"VALUES({', '.join('?' * (len(QUOTA_COLUMNS) + 1))})",
        [(*q, sender) for q in snapshot["quota"]],
    )
    stats["quota"] = conn.total_changes - before

    machine = snapshot["machine"]
    conn.execute(
        "INSERT INTO machines(id, label, os, first_seen_at, app_version, accounts, snapshot_at, synced_at) "
        "VALUES(?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET label=excluded.label, os=excluded.os, "
        "app_version=excluded.app_version, accounts=excluded.accounts, snapshot_at=excluded.snapshot_at, "
        "synced_at=excluded.synced_at",
        (sender, machine["label"], machine["os"], now, snapshot.get("app_version"), json.dumps(snapshot["accounts"]),
         snapshot["taken_at"], now),
    )
    return {"stale": False, **stats}


def check(snapshot: dict, *, settings: Settings) -> None:
    if snapshot.get("format") != FORMAT:
        raise SyncRefused(f"unknown snapshot format {snapshot.get('format')!r}; update TokenLedger on every machine")
    if snapshot["timezone"] != settings.timezone:
        raise SyncRefused(
            f"machine {snapshot['machine']['label']} buckets days in {snapshot['timezone']}, this one in "
            f"{settings.timezone}; set the same timezone in data/settings.json on both"
        )
    if snapshot["grace_days"] != settings.grace_days:
        raise SyncRefused(
            f"machine {snapshot['machine']['label']} settles days after {snapshot['grace_days']} days, this one after "
            f"{settings.grace_days}; set the same grace_days in data/settings.json on both"
        )
    columns = snapshot["columns"]
    if (
        tuple(columns["rows"]) != ROW_COLUMNS
        or tuple(columns["revisions"]) != REVISION_COLUMNS
        or tuple(columns["captures"]) != CAPTURE_COLUMNS
        or tuple(columns["quota"]) != QUOTA_COLUMNS
    ):
        raise SyncRefused("snapshot columns differ from this version; update TokenLedger on every machine")


def _series_key(key: list, *, sender: str, me: str) -> SeriesKey:
    source, provider, machine, scope = key
    scope = Scope(scope)
    if scope == Scope.MACHINE and machine != sender:
        raise SyncRefused(f"a machine may only share its own series, not {machine!r}")
    if machine in (me, LEGACY_ACCOUNT):
        raise SyncRefused(f"a snapshot may not write series {machine!r}")
    return SeriesKey(source, provider, machine, scope)


def _row(values: list) -> dict:
    row = dict(zip(ROW_COLUMNS, values))
    if row["state"] not in (State.OPEN, State.FINAL):
        raise SyncRefused(f"bad row state {row['state']!r}")
    try:
        date.fromisoformat(row["day"])
        # Building the usage checks totals and that cost matches its quality.
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
    except (TypeError, ValueError) as exc:
        raise SyncRefused(f"bad row {row['day']}/{row['model']}: {exc}") from exc
    row["usage"] = usage
    return row


def _origin(row: dict, sender: str) -> str:
    return row["origin"] if row["origin"].startswith("sync:") else f"sync:{sender}:{row['origin']}"


def _write(conn: sqlite3.Connection, sid: int, row: dict, origin: str, existing: sqlite3.Row | None) -> None:
    values = (*row["usage"].values(), row["state"], origin, row["first_seen_at"], row["updated_at"], row["finalized_at"])
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


def _observe(conn: sqlite3.Connection, sid: int, existing: sqlite3.Row, row: dict, origin: str, now: str) -> int:
    """Record that another machine holds a different value for a final row, once per value."""
    usage = row["usage"]
    seen = {(r["observed_total"], r["observed_cost"]) for r in repo.revisions_for(conn, sid, row["day"], row["model"])
            if r["kind"] == "changed" and r["origin"] == origin}
    if (usage.total, usage.cost_micros) in seen:
        return 0
    repo.add_revision(
        conn, sid, row["day"], row["model"], kind="changed", now=now, capture_id=None, origin=origin,
        observed_total=usage.total, observed_cost=usage.cost_micros,
        ledger_total=existing["total"], ledger_cost=existing["cost_micros"],
    )
    return 1


def _mirror(conn: sqlite3.Connection, sid: int, row: dict, sender: str, now: str, stats: dict) -> None:
    existing = repo.ledger_row(conn, sid, row["day"], row["model"])
    if existing is not None and _same(existing, row):
        stats["unchanged"] += 1
        return
    if existing is not None and existing["state"] == State.FINAL:
        corrected = (
            row["state"] == State.FINAL
            and row["origin"].startswith(CORRECTION_ORIGINS)
            and row["updated_at"] > existing["updated_at"]
        )
        if not corrected:
            stats["kept_final"] += 1
            if row["state"] == State.FINAL:
                stats["revisions"] += _observe(conn, sid, existing, row, f"sync:{sender}", now)
            return
    _write(conn, sid, row, _origin(row, sender), existing)
    stats["inserted" if existing is None else "updated"] += 1


def _merge_account(conn: sqlite3.Connection, sid: int, row: dict, sender: str, now: str, stats: dict) -> None:
    existing = repo.ledger_row(conn, sid, row["day"], row["model"])
    if existing is not None and _same(existing, row):
        stats["unchanged"] += 1
        return
    if existing is not None and existing["state"] == State.FINAL:
        stats["kept_final"] += 1
        if row["state"] == State.FINAL:
            stats["revisions"] += _observe(conn, sid, existing, row, f"sync:{sender}", now)
        return
    if existing is not None and row["state"] == State.OPEN and row["updated_at"] <= existing["updated_at"]:
        stats["unchanged"] += 1  # an older reading of the same account
        return
    _write(conn, sid, row, _origin(row, sender), existing)
    stats["inserted" if existing is None else "updated"] += 1


def _add_revision(conn: sqlite3.Connection, sid: int, rev: dict, sender: str, ref: str) -> int:
    # The same revision under another reference: the sender restored it into a recreated ledger.
    held = conn.execute(
        "SELECT 1 FROM revisions WHERE series_id=? AND day=? AND model=? AND kind=? AND observed_at=? "
        "AND observed_total IS ? AND sync_ref IS NOT NULL",
        (sid, rev["day"], rev["model"], rev["kind"], rev["observed_at"], rev["observed_total"]),
    ).fetchone()
    if held is not None:
        return 0
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
            f"{ref}:{rev['id']}",
        ),
    )
    return conn.total_changes - before


def _add_capture(conn: sqlite3.Connection, sids: list[int], values: list, sender: str, ref: str) -> int:
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
            f"{ref}:{cap['id']}",
        ),
    )
    return conn.total_changes - before


# -- this machine's own snapshot ----------------------------------------------------------


def restore(conn: sqlite3.Connection, snapshot: dict, *, settings: Settings, now: str) -> dict:
    """Bring back what this machine shared earlier and its ledger no longer has. Call
    inside a transaction.

    * a row missing here is added as it was
    * an open row here takes the shared row when that one is final
    * a final row here that was settled later than the shared one, with a different value,
      takes the shared value; this ledger's value is kept as a ``replaced`` revision
    * open rows here stay: this machine's latest capture is the better reading
    * revisions missing here are added; quota samples are added once
    """
    check(snapshot, settings=settings)
    if snapshot["machine"]["id"] != settings.machine.id:
        raise SyncRefused("snapshot belongs to another machine")
    stats = {"inserted": 0, "updated": 0, "kept": 0, "revisions": 0, "quota": 0}
    for entry in snapshot["series"]:
        source, provider, machine, scope = entry["key"]
        sid = repo.series_id(conn, SeriesKey(source, provider, machine, Scope(scope)))
        for values in entry["rows"]:
            row = _row(values)
            existing = repo.ledger_row(conn, sid, row["day"], row["model"])
            if existing is None:
                _write(conn, sid, row, row["origin"], None)
                stats["inserted"] += 1
            elif _same(existing, row) or row["state"] != State.FINAL:
                stats["kept"] += 1
            elif existing["state"] == State.OPEN:
                _write(conn, sid, row, row["origin"], existing)
                stats["updated"] += 1
            elif (existing["finalized_at"] or "") > (row["finalized_at"] or ""):
                repo.add_revision(
                    conn, sid, row["day"], row["model"], kind="replaced", now=now, capture_id=None,
                    origin=existing["origin"], observed_total=row["total"], observed_cost=row["cost_micros"],
                    ledger_total=existing["total"], ledger_cost=existing["cost_micros"],
                )
                _write(conn, sid, row, row["origin"], existing)
                stats["updated"] += 1
                stats["revisions"] += 1
            else:
                stats["kept"] += 1
        for values in entry["revisions"]:
            rev = dict(zip(REVISION_COLUMNS, values))
            held = conn.execute(
                "SELECT 1 FROM revisions WHERE series_id=? AND day=? AND model=? AND kind=? AND observed_at=?",
                (sid, rev["day"], rev["model"], rev["kind"], rev["observed_at"]),
            ).fetchone()
            if held is None:
                repo.add_revision(
                    conn, sid, rev["day"], rev["model"], kind=rev["kind"], now=rev["observed_at"], capture_id=None,
                    origin=rev["origin"], observed_total=rev["observed_total"], observed_cost=rev["observed_cost"],
                    ledger_total=rev["ledger_total"], ledger_cost=rev["ledger_cost"],
                )
                stats["revisions"] += 1
    before = conn.total_changes
    conn.executemany(
        f"INSERT OR IGNORE INTO quota_samples({', '.join(QUOTA_COLUMNS)}) VALUES({', '.join('?' * len(QUOTA_COLUMNS))})",
        [tuple(q) for q in snapshot["quota"]],
    )
    stats["quota"] = conn.total_changes - before
    return stats
