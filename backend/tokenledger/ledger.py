"""Finalization rules: how observations become ledger rows.

A day D is *open* on D and the following ``grace_days - 1`` days; every capture
overwrites an open day with the latest values.

The first capture after the grace period *settles* the day, deciding on the
all-model total row (``DAY_TOTAL``):

* the source reports at least as many tokens as the ledger holds: take the
  source's rows for that day (late usage is counted), then freeze them
* the source reports fewer, or no longer has the day: keep the ledger's rows,
  freeze them, and record what the source said as a revision

Settling happens per series, at that series' own next capture, so however long
the gap between captures, a day is never frozen with a partial value.

After that the rows are *final* and never change on their own. Later
observations that disagree (including the day disappearing) are only written to
``revisions``, once per distinct observed value.

Backfill is the one deliberate exception: it writes final rows directly and
may replace a final row, recording the replaced value as a ``replaced``
revision.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .config import Settings
from .models import DAY_TOTAL, CaptureResult, DayUsage, SeriesKey, State
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
    ctx = _Ctx(
        conn=conn,
        sid=repo.series_id(conn, result.series),
        now=iso(now_utc),
        origin=f"capture:{capture_id}",
        capture_id=capture_id,
        stats=stats,
    )
    today = local_today(settings, now_utc)
    _check_unique(result.rows)

    by_day: dict[str, list[DayUsage]] = defaultdict(list)
    for usage in result.rows:
        by_day[usage.day].append(usage)

    for day, rows in sorted(by_day.items()):
        total = next((u for u in rows if u.model == DAY_TOTAL), None)
        if total is None:
            raise ValueError(f"source reported {day} without an all-model total row")
        held = repo.ledger_row(conn, ctx.sid, day, DAY_TOTAL)
        if is_open(day, today, settings.grace_days):
            _merge_open_day(ctx, rows)
        elif held is None:
            _insert_final_day(ctx, rows)
        elif held["state"] == State.OPEN:
            _settle_day(ctx, day, rows, accept=total.total >= held["total"])
        else:
            _observe_final_day(ctx, rows)

    if result.window_start and result.window_end:
        for row in repo.ledger_rows_in_window(conn, ctx.sid, result.window_start, result.window_end):
            if row["day"] in by_day and any(u.model == row["model"] for u in by_day[row["day"]]):
                continue
            stats.missing.append(f"{row['day']}/{row['model']}")
            if row["state"] == State.OPEN and not is_open(row["day"], today, settings.grace_days):
                repo.finalize_row(conn, ctx.sid, row["day"], row["model"], ctx.now)
                stats.finalized += 1
            if _record_revision(ctx, row["day"], row["model"], row, kind="missing", observed=None):
                stats.revisions += 1

    return stats


@dataclass
class _Ctx:
    conn: sqlite3.Connection
    sid: int
    now: str
    origin: str
    capture_id: int | None
    stats: ApplyStats


def _merge_open_day(ctx: _Ctx, rows: list[DayUsage]) -> None:
    """Inside the grace period: the latest capture wins."""
    for usage in rows:
        existing = repo.ledger_row(ctx.conn, ctx.sid, usage.day, usage.model)
        if existing is None:
            repo.insert_ledger(ctx.conn, ctx.sid, usage, state=State.OPEN, origin=ctx.origin, now=ctx.now)
            ctx.stats.inserted += 1
        elif existing["state"] == State.FINAL:
            _observe(ctx, existing, usage)
        elif repo.row_values(existing) != usage.values():
            repo.update_ledger_values(ctx.conn, ctx.sid, usage, origin=ctx.origin, now=ctx.now)
            ctx.stats.updated += 1
        else:
            ctx.stats.unchanged += 1


def _insert_final_day(ctx: _Ctx, rows: list[DayUsage]) -> None:
    """First sighting of a day that is already past its grace period."""
    for usage in rows:
        existing = repo.ledger_row(ctx.conn, ctx.sid, usage.day, usage.model)
        if existing is None:
            repo.insert_ledger(ctx.conn, ctx.sid, usage, state=State.FINAL, origin=ctx.origin, now=ctx.now)
            ctx.stats.inserted += 1
        else:
            _observe(ctx, existing, usage)


def _settle_day(ctx: _Ctx, day: str, rows: list[DayUsage], *, accept: bool) -> None:
    """First capture after the grace period: take the source's rows if they did not shrink, then freeze."""
    if accept:
        for usage in rows:
            existing = repo.ledger_row(ctx.conn, ctx.sid, day, usage.model)
            if existing is None:
                repo.insert_ledger(ctx.conn, ctx.sid, usage, state=State.FINAL, origin=ctx.origin, now=ctx.now)
                ctx.stats.inserted += 1
                continue
            if existing["state"] == State.FINAL:
                _observe(ctx, existing, usage)
                continue
            if repo.row_values(existing) != usage.values():
                repo.update_ledger_values(ctx.conn, ctx.sid, usage, origin=ctx.origin, now=ctx.now)
                ctx.stats.updated += 1
            else:
                ctx.stats.unchanged += 1
            repo.finalize_row(ctx.conn, ctx.sid, day, usage.model, ctx.now)
            ctx.stats.finalized += 1
        return
    # The source now reports less than the ledger holds: keep what we have.
    for usage in rows:
        existing = repo.ledger_row(ctx.conn, ctx.sid, day, usage.model)
        if existing is None:
            if _record_revision(ctx, day, usage.model, None, kind="changed", observed=usage):
                ctx.stats.revisions += 1
            continue
        if existing["state"] == State.OPEN:
            repo.finalize_row(ctx.conn, ctx.sid, day, usage.model, ctx.now)
            ctx.stats.finalized += 1
        _observe(ctx, existing, usage)


def _observe_final_day(ctx: _Ctx, rows: list[DayUsage]) -> None:
    """A settled day: never change it, only note disagreements."""
    for usage in rows:
        existing = repo.ledger_row(ctx.conn, ctx.sid, usage.day, usage.model)
        if existing is None:
            if _record_revision(ctx, usage.day, usage.model, None, kind="changed", observed=usage):
                ctx.stats.revisions += 1
        else:
            _observe(ctx, existing, usage)


def _observe(ctx: _Ctx, existing: sqlite3.Row, usage: DayUsage) -> None:
    if _record_revision(ctx, usage.day, usage.model, existing, kind="changed", observed=usage):
        ctx.stats.revisions += 1
    else:
        ctx.stats.unchanged += 1


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


def _check_unique(rows: list[DayUsage]) -> None:
    keys = [(u.day, u.model) for u in rows]
    if len(keys) != len(set(keys)):
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        raise ValueError(f"source produced duplicate day/model rows: {dupes[:5]}")


def _baseline(conn: sqlite3.Connection, sid: int, day: str, model: str, row: sqlite3.Row | None) -> tuple:
    """What the source said most recently about a key, as (kind, total, cost).

    * after a recorded observation: that observation
    * after a backfill replaced the row: the source's value at that moment
    * a row written by a capture: its own value
    * a row inserted by a backfill, or no row at all: the source never had it, so "missing"
    """
    last = repo.last_revision(conn, sid, day, model)
    if last is not None:
        if last["kind"] == "replaced":
            return ("changed", last["ledger_total"], last["ledger_cost"])
        return (last["kind"], last["observed_total"], last["observed_cost"])
    if row is None or row["origin"].startswith("backfill:"):
        return ("missing", None, None)
    return ("changed", row["total"], row["cost_micros"])


def _record_revision(
    ctx: _Ctx,
    day: str,
    model: str,
    row: sqlite3.Row | None,
    *,
    kind: str,
    observed: DayUsage | None,
) -> bool:
    """Record what the source now says about a key, unless it is what it said last time."""
    obs_total = observed.total if observed else None
    obs_cost = observed.cost_micros if observed else None
    if _baseline(ctx.conn, ctx.sid, day, model, row) == (kind, obs_total, obs_cost):
        return False
    repo.add_revision(
        ctx.conn,
        ctx.sid,
        day,
        model,
        kind=kind,
        now=ctx.now,
        capture_id=ctx.capture_id,
        origin=ctx.origin,
        observed_total=obs_total,
        observed_cost=obs_cost,
        ledger_total=row["total"] if row is not None else None,
        ledger_cost=row["cost_micros"] if row is not None else None,
    )
    return True
