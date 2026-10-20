"""Run sources and merge their output into the ledger.

Each source runs independently: one failing source is recorded as a failed
capture and the others still run. Every successful result is archived raw
before it is merged, and the merge happens in a single transaction per series.
"""

from __future__ import annotations

import sqlite3
import threading
import traceback
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Callable

from . import build_version, ledger, rawstore
from .config import Paths, Settings
from .sources import QuotaSource, SourceContext, SourceError, UsageSource
from .models import CaptureResult, Scope, SeriesKey
from .sources.codex_server import LEGACY_ACCOUNT
from .store import repo

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Outcome:
    source: str
    ok: bool
    series: list[dict] = field(default_factory=list)
    error: str | None = None

    def as_dict(self) -> dict:
        return {"source": self.source, "ok": self.ok, "series": self.series, "error": self.error}


# One capture at a time in this process: the dashboard's button and its own schedule share it.
LOCK = threading.Lock()


def run_default(conn: sqlite3.Connection, paths: Paths, settings: Settings) -> list[Outcome] | None:
    """Capture every source this machine has, then sync when signed in to a cloud ledger.
    None without waiting when another capture in this process is running."""
    from . import sources
    from .sync.client import after_capture

    if not LOCK.acquire(blocking=False):
        return None
    try:
        outcomes = run(
            conn, paths, settings,
            usage_sources=sources.default_usage_sources(), quota_sources=sources.default_quota_sources(),
        )
        # The outcome of the sync shows up in the status; a failed sync never fails the capture.
        after_capture(conn, paths, settings)
        return outcomes
    finally:
        LOCK.release()


def last_own_capture(conn: sqlite3.Connection) -> datetime | None:
    """When this machine last started a capture of its sources (any outcome)."""
    row = conn.execute("SELECT MAX(started_at) AS at FROM captures WHERE machine IS NULL AND kind IN ('usage', 'quota')").fetchone()
    return datetime.fromisoformat(row["at"]) if row and row["at"] else None


def run(
    conn: sqlite3.Connection,
    paths: Paths,
    settings: Settings,
    *,
    usage_sources: list[UsageSource],
    quota_sources: list[QuotaSource],
    clock: Clock = utc_now,
) -> list[Outcome]:
    started = clock()
    with repo.transaction(conn):
        repo.ensure_machine(conn, settings.machine, ledger.iso(started), build_version())
    outcomes = [_run_usage(conn, paths, settings, s, clock) for s in usage_sources]
    outcomes += [_run_quota(conn, settings, s, clock) for s in quota_sources]
    with repo.transaction(conn):
        # Which account-wide series this machine reads: the views add its own logs on top of
        # those accounts only when it reads none (see views.py).
        repo.set_machine_accounts(conn, settings.machine.id, repo.accounts_read(conn, LEGACY_ACCOUNT))
    return outcomes


def _run_usage(conn, paths: Paths, settings: Settings, source: UsageSource, clock: Clock) -> Outcome:
    now = clock()
    try:
        results = source.collect(SourceContext(settings=settings, now_utc=now))
    except SourceError as exc:
        _record_failure(conn, "usage", source.id, now, str(exc))
        return Outcome(source.id, False, error=str(exc))
    except Exception as exc:  # noqa: BLE001 - recorded and reported, never swallowed
        message = f"{type(exc).__name__}: {exc}"
        _record_failure(conn, "usage", source.id, now, message + "\n" + traceback.format_exc(limit=5))
        return Outcome(source.id, False, error=message)

    outcome = Outcome(source.id, True)
    for result in results:
        now = clock()
        raw_path = rawstore.write(paths.raw, source.id, result.raw_name, now, result.raw)
        try:
            with repo.transaction(conn):
                result, account = _account_series(conn, result, ledger.iso(now))
                sid = repo.series_id(conn, result.series)
                cid = repo.start_capture(conn, "usage", source.id, sid, ledger.iso(now))
                stats = ledger.apply_capture(conn, result, settings=settings, now_utc=now, capture_id=cid)
                repo.finish_capture(
                    conn,
                    cid,
                    ledger.iso(clock()),
                    status="ok",
                    tool_version=result.tool_version,
                    raw_path=str(raw_path.relative_to(paths.data)),
                    stats={**stats.as_dict(), "rows": len(result.rows), **_meta(result.meta), **account},
                )
        except Exception as exc:  # noqa: BLE001
            raw_path.unlink(missing_ok=True)
            message = f"{result.series.provider}: {type(exc).__name__}: {exc}"
            _record_failure(conn, "usage", source.id, now, message)
            outcome.ok = False
            outcome.error = message
            continue
        outcome.series.append({"provider": result.series.provider, "scope": str(result.series.scope), **stats.as_dict()})
    return outcome


def _account_series(conn: sqlite3.Connection, result: CaptureResult, now: str) -> tuple[CaptureResult, dict]:
    """Which account-wide series a reading goes to (see sources/codex_server.py), from the
    hashed account names the source found:

    * the account id when there is one
    * else the series an earlier reading with the same email went to
    * else the email
    * else (``account/read`` told nothing) the series this machine's last reading went to,
      or the unnamed series of older ledgers

    Once the account is known, a series of the same account under a name used before (the
    unnamed series, or the email before an id was returned) is folded into it. Folds of
    named series are remembered, so the other machines fold their copies too
    (``sync/snapshot.py``)."""
    keys = result.meta.get("account_keys")
    if result.series.scope != Scope.ACCOUNT or keys is None:
        return result, {}
    email = keys.get("email")
    name = keys.get("id") or _series_for_email(conn, result.series, email) or email or _last_account(conn, result.series)
    stats: dict = {"account_keys": keys}
    if name is None:
        name = LEGACY_ACCOUNT
        stats["warning"] = "account/read named no account; usage kept under the unnamed series until it does"
    target = replace(result.series, machine=name)
    folded = []
    if name != LEGACY_ACCOUNT:
        for old in dict.fromkeys([LEGACY_ACCOUNT, email]):
            if not old or old == name:
                continue
            sid = repo.find_series(conn, replace(result.series, machine=old))
            if sid is None:
                continue
            folded.append({"from": old, **repo.merge_series(conn, sid, repo.series_id(conn, target), now)})
            if old != LEGACY_ACCOUNT:
                repo.add_account_fold(conn, target.source, target.provider, old, name)
    if folded:
        stats["folded"] = folded
    return replace(result, series=target), stats


def _series_for_email(conn: sqlite3.Connection, series: SeriesKey, email: str | None) -> str | None:
    if not email:
        return None
    row = conn.execute(
        "SELECT s.machine FROM captures c JOIN series s ON s.id = c.series_id WHERE c.source = ? AND s.provider = ? "
        "AND s.scope = 'account' AND c.status = 'ok' AND json_extract(c.stats, '$.account_keys.email') = ? "
        "AND json_extract(c.stats, '$.account_keys.id') IS NOT NULL ORDER BY c.started_at DESC LIMIT 1",
        (series.source, series.provider, email),
    ).fetchone()
    return row["machine"] if row else None


def _last_account(conn: sqlite3.Connection, series: SeriesKey) -> str | None:
    row = conn.execute(
        "SELECT s.machine FROM captures c JOIN series s ON s.id = c.series_id WHERE c.machine IS NULL "
        "AND c.source = ? AND s.provider = ? AND s.scope = 'account' AND c.status = 'ok' AND s.machine != ? "
        "ORDER BY c.started_at DESC LIMIT 1",
        (series.source, series.provider, LEGACY_ACCOUNT),
    ).fetchone()
    return row["machine"] if row else None


def _run_quota(conn, settings: Settings, source: QuotaSource, clock: Clock) -> Outcome:
    now = clock()
    try:
        capture = source.collect(SourceContext(settings=settings, now_utc=now))
        with repo.transaction(conn):
            cid = repo.start_capture(conn, "quota", source.id, None, ledger.iso(now))
            added = repo.insert_quota_samples(conn, capture.samples)
            repo.finish_capture(
                conn,
                cid,
                ledger.iso(clock()),
                status="ok",
                tool_version=capture.tool_version,
                stats={"samples_seen": len(capture.samples), "samples_added": added},
            )
    except Exception as exc:  # noqa: BLE001
        message = f"{type(exc).__name__}: {exc}" if not isinstance(exc, SourceError) else str(exc)
        _record_failure(conn, "quota", source.id, now, message)
        return Outcome(source.id, False, error=message)
    return Outcome(source.id, True, series=[{"samples_seen": len(capture.samples), "samples_added": added}])


def _record_failure(conn: sqlite3.Connection, kind: str, source: str, now: datetime, message: str) -> None:
    with repo.transaction(conn):
        cid = repo.start_capture(conn, kind, source, None, ledger.iso(now))
        repo.finish_capture(conn, cid, ledger.iso(now), status="error", error=message)


def _meta(meta: dict) -> dict:
    keep = {k: meta[k] for k in ("history_days", "coverage", "files", "requests", "skipped_lines") if k in meta}
    summary = meta.get("summary")
    if isinstance(summary, dict) and "lifetimeTokens" in summary:
        keep["lifetime_tokens"] = summary["lifetimeTokens"]
    return keep
