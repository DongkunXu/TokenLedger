"""Row-level access to the ledger database. No business rules live here."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Iterator

from ..config import Machine
from ..models import CostQuality, DayUsage, QuotaSample, Scope, SeriesKey, State

LEDGER_VALUE_COLUMNS = (
    "total",
    "input_uncached",
    "cache_read",
    "cache_write",
    "output",
    "reasoning",
    "cost_micros",
    "cost_quality",
)


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def ensure_machine(conn: sqlite3.Connection, machine: Machine, now: str) -> None:
    conn.execute(
        "INSERT INTO machines(id, label, os, first_seen_at) VALUES(?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET label=excluded.label, os=excluded.os",
        (machine.id, machine.label, machine.os, now),
    )


def series_id(conn: sqlite3.Connection, key: SeriesKey) -> int:
    conn.execute(
        "INSERT OR IGNORE INTO series(source, provider, machine, scope) VALUES(?, ?, ?, ?)",
        (key.source, key.provider, key.machine, str(key.scope)),
    )
    row = conn.execute(
        "SELECT id FROM series WHERE source=? AND provider=? AND machine=? AND scope=?",
        (key.source, key.provider, key.machine, str(key.scope)),
    ).fetchone()
    return int(row["id"])


def series_key(conn: sqlite3.Connection, sid: int) -> SeriesKey:
    row = conn.execute("SELECT source, provider, machine, scope FROM series WHERE id=?", (sid,)).fetchone()
    return SeriesKey(row["source"], row["provider"], row["machine"], Scope(row["scope"]))


def start_capture(conn: sqlite3.Connection, kind: str, source: str, sid: int | None, now: str) -> int:
    cur = conn.execute(
        "INSERT INTO captures(kind, source, series_id, started_at, status) VALUES(?, ?, ?, ?, 'running')",
        (kind, source, sid, now),
    )
    return int(cur.lastrowid)


def finish_capture(
    conn: sqlite3.Connection,
    capture_id: int,
    now: str,
    *,
    status: str,
    tool_version: str | None = None,
    raw_path: str | None = None,
    stats: dict | None = None,
    error: str | None = None,
) -> None:
    conn.execute(
        "UPDATE captures SET finished_at=?, status=?, tool_version=?, raw_path=?, stats=?, error=? WHERE id=?",
        (now, status, tool_version, raw_path, json.dumps(stats) if stats is not None else None, error, capture_id),
    )


def ledger_row(conn: sqlite3.Connection, sid: int, day: str, model: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM ledger WHERE series_id=? AND day=? AND model=?", (sid, day, model)
    ).fetchone()


def row_values(row: sqlite3.Row) -> tuple:
    return tuple(row[c] for c in LEDGER_VALUE_COLUMNS)


def row_to_usage(row: sqlite3.Row) -> DayUsage:
    return DayUsage(
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


def insert_ledger(
    conn: sqlite3.Connection, sid: int, usage: DayUsage, *, state: State, origin: str, now: str
) -> None:
    conn.execute(
        "INSERT INTO ledger(series_id, day, model, total, input_uncached, cache_read, cache_write, output, "
        "reasoning, cost_micros, cost_quality, state, origin, first_seen_at, updated_at, finalized_at) "
        "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            sid,
            usage.day,
            usage.model,
            *usage.values(),
            str(state),
            origin,
            now,
            now,
            now if state == State.FINAL else None,
        ),
    )


def update_ledger_values(conn: sqlite3.Connection, sid: int, usage: DayUsage, *, origin: str, now: str) -> None:
    conn.execute(
        "UPDATE ledger SET total=?, input_uncached=?, cache_read=?, cache_write=?, output=?, reasoning=?, "
        "cost_micros=?, cost_quality=?, origin=?, updated_at=? WHERE series_id=? AND day=? AND model=?",
        (*usage.values(), origin, now, sid, usage.day, usage.model),
    )


def finalize_row(conn: sqlite3.Connection, sid: int, day: str, model: str, now: str) -> None:
    conn.execute(
        "UPDATE ledger SET state='final', finalized_at=? WHERE series_id=? AND day=? AND model=? AND state='open'",
        (now, sid, day, model),
    )


def open_rows_due(conn: sqlite3.Connection, last_open_day_excl: str) -> list[sqlite3.Row]:
    """Open rows whose day is strictly before ``last_open_day_excl`` (the oldest day still open)."""
    return conn.execute(
        "SELECT series_id, day, model FROM ledger WHERE state='open' AND day < ?", (last_open_day_excl,)
    ).fetchall()


def ledger_rows_in_window(conn: sqlite3.Connection, sid: int, start: str, end: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM ledger WHERE series_id=? AND day BETWEEN ? AND ?", (sid, start, end)
    ).fetchall()


def last_revision(conn: sqlite3.Connection, sid: int, day: str, model: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM revisions WHERE series_id=? AND day=? AND model=? ORDER BY id DESC LIMIT 1",
        (sid, day, model),
    ).fetchone()


def add_revision(
    conn: sqlite3.Connection,
    sid: int,
    day: str,
    model: str,
    *,
    kind: str,
    now: str,
    capture_id: int | None,
    origin: str,
    observed_total: int | None,
    observed_cost: int | None,
    ledger_total: int | None,
    ledger_cost: int | None,
) -> None:
    conn.execute(
        "INSERT INTO revisions(series_id, day, model, kind, observed_at, capture_id, origin, observed_total, "
        "observed_cost, ledger_total, ledger_cost) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (sid, day, model, kind, now, capture_id, origin, observed_total, observed_cost, ledger_total, ledger_cost),
    )


def insert_quota_samples(conn: sqlite3.Connection, samples: list[QuotaSample]) -> int:
    before = conn.total_changes
    conn.executemany(
        "INSERT OR IGNORE INTO quota_samples(provider, account, window, captured_at, window_minutes, "
        "used_percent, resets_at, source) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (s.provider, s.account, s.window, s.captured_at, s.window_minutes, s.used_percent, s.resets_at, s.source)
            for s in samples
        ],
    )
    return conn.total_changes - before
