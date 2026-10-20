"""Run sources and merge their output into the ledger.

Each source runs independently: one failing source is recorded as a failed
capture and the others still run. Every successful result is archived raw
before it is merged, and the merge happens in a single transaction per series.
"""

from __future__ import annotations

import sqlite3
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from . import ledger, rawstore
from .config import Paths, Settings
from .sources import QuotaSource, SourceContext, SourceError, UsageSource
from .models import Scope, SeriesKey
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
        repo.ensure_machine(conn, settings.machine, ledger.iso(started))
    outcomes = [_run_usage(conn, paths, settings, s, clock) for s in usage_sources]
    outcomes += [_run_quota(conn, settings, s, clock) for s in quota_sources]
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
                adopted = _adopt_legacy_account(conn, result.series, ledger.iso(now))
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
                    stats={**stats.as_dict(), "rows": len(result.rows), **_meta(result.meta), **adopted},
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


def _adopt_legacy_account(conn: sqlite3.Connection, series: SeriesKey, now: str) -> dict:
    """The first capture that knows which account a ledger's unnamed ``account`` series
    belongs to folds it into that account's series (see sources/codex_server.py)."""
    if series.scope != Scope.ACCOUNT or series.machine == LEGACY_ACCOUNT:
        return {}
    legacy = repo.find_series(conn, SeriesKey(series.source, series.provider, LEGACY_ACCOUNT, Scope.ACCOUNT))
    if legacy is None:
        return {}
    merged = repo.merge_series(conn, legacy, repo.series_id(conn, series), now)
    return {"adopted_legacy_account": merged}


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
