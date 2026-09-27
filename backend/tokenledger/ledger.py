"""Finalization rules: how observations become ledger rows.

A day D is *open* on D and the following ``grace_days - 1`` days; every capture
overwrites an open row with the latest value. After that the row is *final* and
its value never changes on its own. Later observations that disagree with a
final row (including the day disappearing from the source) are only written to
``revisions``, once per distinct observed value.

Backfill is the one deliberate exception: it writes final rows directly and
may replace a final row, recording the replaced value as a ``replaced``
revision.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .config import Settings
from .models import CaptureResult, DayUsage, SeriesKey, State
from .store import repo


@dataclass
class ApplyStats:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    finalized: int = 0
    revisions: int = 0
    skipped_open: int = 0
    missing: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "inserted": self.inserted,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "finalized": self.finalized,
            "revisions": self.revisions,
            "skipped_open": self.skipped_open,
            "missing_days": len(self.missing),
        }


def local_today(settings: Settings, now_utc: datetime) -> date:
    return now_utc.astimezone(settings.tz).date()


def oldest_open_day(today: date, grace_days: int) -> date:
    return today - timedelta(days=grace_days - 1)


def is_open(day: str, today: date, grace_days: int) -> bool:
    return date.fromisoformat(day) >= oldest_open_day(today, grace_days)


def iso(now_utc: datetime) -> str:
    return now_utc.isoformat(timespec="seconds")


def apply_capture(
    conn: sqlite3.Connection,
    result: CaptureResult,
    *,
    settings: Settings,
    now_utc: datetime,
    capture_id: int,
) -> ApplyStats:
    """Merge one capture into the ledger. Call inside a transaction."""
    stats = ApplyStats()
    now = iso(now_utc)
    today = local_today(settings, now_utc)
    sid = repo.series_id(conn, result.series)
    origin = f"capture:{capture_id}"

    _check_unique(result.rows)
    # Freeze first: a row whose grace ended since the last capture must not take this capture's value.
    stats.finalized = finalize_due(conn, settings=settings, now_utc=now_utc)
    seen: set[tuple[str, str]] = set()
    for usage in result.rows:
        seen.add((usage.day, usage.model))
        existing = repo.ledger_row(conn, sid, usage.day, usage.model)
        day_open = is_open(usage.day, today, settings.grace_days)
        if existing is None:
            state = State.OPEN if day_open else State.FINAL
            repo.insert_ledger(conn, sid, usage, state=state, origin=origin, now=now)
            stats.inserted += 1
        elif existing["state"] == State.OPEN:
            if repo.row_values(existing) != usage.values():
                repo.update_ledger_values(conn, sid, usage, origin=origin, now=now)
                stats.updated += 1
            else:
                stats.unchanged += 1
        elif _record_revision(
            conn, existing, kind="changed", observed=usage, now=now, capture_id=capture_id, origin=origin
        ):
            stats.revisions += 1
        else:
            stats.unchanged += 1

    if result.window_start and result.window_end:
        for row in repo.ledger_rows_in_window(conn, sid, result.window_start, result.window_end):
            if (row["day"], row["model"]) in seen:
                continue
            stats.missing.append(f"{row['day']}/{row['model']}")
            if _record_revision(conn, row, kind="missing", observed=None, now=now, capture_id=capture_id, origin=origin):
                stats.revisions += 1

    return stats


def apply_backfill(
    conn: sqlite3.Connection,
    series: SeriesKey,
    rows: list[DayUsage],
    *,
    settings: Settings,
    now_utc: datetime,
    capture_id: int,
    origin: str,
) -> ApplyStats:
    """Write backfilled rows as final values. Open days are left to captures. Call inside a transaction."""
    stats = ApplyStats()
    now = iso(now_utc)
    today = local_today(settings, now_utc)
    sid = repo.series_id(conn, series)
    _check_unique(rows)
    stats.finalized = finalize_due(conn, settings=settings, now_utc=now_utc)
    for usage in rows:
        if is_open(usage.day, today, settings.grace_days):
            stats.skipped_open += 1
            continue
        existing = repo.ledger_row(conn, sid, usage.day, usage.model)
        if existing is None:
            repo.insert_ledger(conn, sid, usage, state=State.FINAL, origin=origin, now=now)
            stats.inserted += 1
        elif repo.row_values(existing) == usage.values():
            stats.unchanged += 1
        else:
            repo.add_revision(
                conn,
                sid,
                usage.day,
                usage.model,
                kind="replaced",
                now=now,
                capture_id=capture_id,
                origin=existing["origin"],
                observed_total=usage.total,
                observed_cost=usage.cost_micros,
                ledger_total=existing["total"],
                ledger_cost=existing["cost_micros"],
            )
            repo.update_ledger_values(conn, sid, usage, origin=origin, now=now)
            repo.finalize_row(conn, sid, usage.day, usage.model, now)
            stats.updated += 1
            stats.revisions += 1
    return stats


def finalize_due(conn: sqlite3.Connection, *, settings: Settings, now_utc: datetime) -> int:
    """Freeze every open row whose grace period has ended."""
    today = local_today(settings, now_utc)
    boundary = oldest_open_day(today, settings.grace_days).isoformat()
    due = repo.open_rows_due(conn, boundary)
    now = iso(now_utc)
    for row in due:
        repo.finalize_row(conn, row["series_id"], row["day"], row["model"], now)
    return len(due)


def _check_unique(rows: list[DayUsage]) -> None:
    keys = [(u.day, u.model) for u in rows]
    if len(keys) != len(set(keys)):
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        raise ValueError(f"source produced duplicate day/model rows: {dupes[:5]}")


def _baseline(conn: sqlite3.Connection, row: sqlite3.Row) -> tuple:
    """What the source said most recently about a row, as (kind, total, cost).

    * after a recorded observation: that observation
    * after a backfill replaced the row: the source's value at that moment
    * a row written by a capture: its own value
    * a row inserted by a backfill: the source never had it, so "missing"
    """
    last = repo.last_revision(conn, row["series_id"], row["day"], row["model"])
    if last is not None:
        if last["kind"] == "replaced":
            return ("changed", last["ledger_total"], last["ledger_cost"])
        return (last["kind"], last["observed_total"], last["observed_cost"])
    if row["origin"].startswith("backfill:"):
        return ("missing", None, None)
    return ("changed", row["total"], row["cost_micros"])


def _record_revision(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    kind: str,
    observed: DayUsage | None,
    now: str,
    capture_id: int,
    origin: str,
) -> bool:
    """Record what the source now says about a row, unless it is what it said last time."""
    obs_total = observed.total if observed else None
    obs_cost = observed.cost_micros if observed else None
    if _baseline(conn, row) == (kind, obs_total, obs_cost):
        return False
    repo.add_revision(
        conn,
        row["series_id"],
        row["day"],
        row["model"],
        kind=kind,
        now=now,
        capture_id=capture_id,
        origin=origin,
        observed_total=obs_total,
        observed_cost=obs_cost,
        ledger_total=row["total"],
        ledger_cost=row["cost_micros"],
    )
    return True
