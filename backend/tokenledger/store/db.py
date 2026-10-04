"""SQLite connection and schema migrations.

Migrations are append-only: each entry runs once, in order, and the applied
version is stored in ``meta.schema_version``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

MIGRATIONS: list[str] = [
    # 1: initial schema
    """
    CREATE TABLE meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );

    CREATE TABLE machines (
        id            TEXT PRIMARY KEY,
        label         TEXT NOT NULL,
        os            TEXT NOT NULL,
        first_seen_at TEXT NOT NULL
    );

    CREATE TABLE series (
        id       INTEGER PRIMARY KEY,
        source   TEXT NOT NULL,
        provider TEXT NOT NULL,
        machine  TEXT NOT NULL,
        scope    TEXT NOT NULL CHECK (scope IN ('machine', 'account')),
        UNIQUE (source, provider, machine, scope)
    );

    CREATE TABLE captures (
        id           INTEGER PRIMARY KEY,
        kind         TEXT NOT NULL CHECK (kind IN ('usage', 'quota', 'backfill')),
        source       TEXT NOT NULL,
        series_id    INTEGER REFERENCES series(id),
        started_at   TEXT NOT NULL,
        finished_at  TEXT,
        status       TEXT NOT NULL CHECK (status IN ('running', 'ok', 'error')),
        tool_version TEXT,
        raw_path     TEXT,
        stats        TEXT,
        error        TEXT
    );
    CREATE INDEX captures_started_idx ON captures(started_at);

    CREATE TABLE ledger (
        series_id      INTEGER NOT NULL REFERENCES series(id),
        day            TEXT NOT NULL,
        model          TEXT NOT NULL,
        total          INTEGER NOT NULL,
        input_uncached INTEGER,
        cache_read     INTEGER,
        cache_write    INTEGER,
        output         INTEGER,
        reasoning      INTEGER,
        cost_micros    INTEGER,
        cost_quality   TEXT NOT NULL,
        state          TEXT NOT NULL CHECK (state IN ('open', 'final')),
        origin         TEXT NOT NULL,
        first_seen_at  TEXT NOT NULL,
        updated_at     TEXT NOT NULL,
        finalized_at   TEXT,
        PRIMARY KEY (series_id, day, model)
    );
    CREATE INDEX ledger_day_idx ON ledger(day);

    CREATE TABLE revisions (
        id             INTEGER PRIMARY KEY,
        series_id      INTEGER NOT NULL REFERENCES series(id),
        day            TEXT NOT NULL,
        model          TEXT NOT NULL,
        kind           TEXT NOT NULL CHECK (kind IN ('changed', 'missing', 'replaced')),
        observed_at    TEXT NOT NULL,
        capture_id     INTEGER REFERENCES captures(id),
        origin         TEXT NOT NULL,
        observed_total INTEGER,
        observed_cost  INTEGER,
        ledger_total   INTEGER,
        ledger_cost    INTEGER
    );
    CREATE INDEX revisions_key_idx ON revisions(series_id, day, model, id);

    CREATE TABLE quota_samples (
        provider       TEXT NOT NULL,
        account        TEXT NOT NULL,
        window         TEXT NOT NULL,
        captured_at    TEXT NOT NULL,
        window_minutes INTEGER,
        used_percent   REAL NOT NULL,
        resets_at      TEXT,
        source         TEXT NOT NULL,
        PRIMARY KEY (provider, account, window, captured_at)
    );
    """,
    # 2: Codex moved from CodexBar to the codex-logs source (2026-10-04). CodexBar's Codex
    # series is no longer captured, so its open days are frozen as they stand; the views
    # read those days from codex-logs.
    """
    UPDATE ledger SET state = 'final', finalized_at = strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now')
    WHERE state = 'open' AND series_id IN (SELECT id FROM series WHERE source = 'codexbar' AND provider = 'codex');
    """,
]


def connect(path: Path, *, cross_thread: bool = False) -> sqlite3.Connection:
    """Open the ledger. ``cross_thread`` allows handing the connection to another thread
    (FastAPI opens it in one worker thread and runs the endpoint in another); it is still
    never used by two threads at the same time."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=not cross_thread)  # explicit transactions only
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def schema_version(conn: sqlite3.Connection) -> int:
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone()
    if not exists:
        return 0
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    return int(row["value"]) if row else 0


def migrate(conn: sqlite3.Connection) -> int:
    current = schema_version(conn)
    if current > len(MIGRATIONS):
        raise RuntimeError(f"database schema v{current} is newer than this code (v{len(MIGRATIONS)})")
    for version in range(current + 1, len(MIGRATIONS) + 1):
        conn.execute("BEGIN IMMEDIATE")
        try:
            for statement in _split(MIGRATIONS[version - 1]):
                conn.execute(statement)
            conn.execute(
                "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(version),),
            )
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    return len(MIGRATIONS)


def _split(script: str) -> list[str]:
    return [s.strip() for s in script.split(";") if s.strip()]
