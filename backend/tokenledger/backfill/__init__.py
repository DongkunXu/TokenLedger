"""One-off imports of history that the regular sources can no longer see."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from .. import ledger, rawstore
from ..config import Paths, Settings
from ..models import DAY_TOTAL, DayUsage, Scope, SeriesKey
from ..sources import SourceContext
from ..sources.codexbar import parse_cost_export
from ..store import repo
from .codexbar_legacy import claude_backfill, codex_backfill, load_catalog

ORIGIN = "backfill:codexbar-legacy"


def legacy_folder(paths: Paths) -> Path:
    folders = sorted((paths.data / "legacy").glob("codexbar-cache-*"))
    if not folders:
        raise FileNotFoundError(f"no preserved CodexBar caches under {paths.data / 'legacy'}")
    return folders[-1]


def latest_export(conn: sqlite3.Connection, paths: Paths, settings: Settings, provider: str, now: datetime) -> list[DayUsage]:
    key = SeriesKey("codexbar", provider, settings.machine.id, Scope.MACHINE)
    sid = repo.series_id(conn, key)
    row = conn.execute(
        "SELECT raw_path FROM captures WHERE series_id=? AND status='ok' AND raw_path IS NOT NULL "
        "ORDER BY id DESC LIMIT 1",
        (sid,),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"no successful codexbar {provider} capture yet; run `tokenledger capture` first")
    raw = rawstore.read(paths.data / row["raw_path"])
    result = parse_cost_export(raw, provider=provider, ctx=SourceContext(settings, now), tool_version="archived")
    return result.rows if result else []


def run_codexbar_legacy(
    conn: sqlite3.Connection, paths: Paths, settings: Settings, *, now_utc: datetime, dry_run: bool
) -> dict:
    folder = legacy_folder(paths)
    catalog = load_catalog(folder)
    claude_base = latest_export(conn, paths, settings, "claude", now_utc)
    codex_base = latest_export(conn, paths, settings, "codex", now_utc)

    claude = claude_backfill(folder, claude_base, catalog)
    codex_rows = codex_backfill(folder, {r.day for r in codex_base}, catalog)

    plan = {
        "folder": str(folder.relative_to(paths.data)),
        "claude": {
            "added_requests": claude.added_requests,
            "added_by_cache": claude.added_by_cache,
            "one_hour_cache_share": round(claude.one_hour_share, 4),
            "by_month_before": _by_month(claude_base),
            "by_month_after": _by_month(_overlay(claude_base, claude.rows)),
        },
        "codex": {"by_month_backfilled": _by_month(codex_rows)},
    }
    if dry_run:
        return plan

    for provider, rows in (("claude", claude.rows), ("codex", codex_rows)):
        series = SeriesKey("codexbar", provider, settings.machine.id, Scope.MACHINE)
        with repo.transaction(conn):
            sid = repo.series_id(conn, series)
            cid = repo.start_capture(conn, "backfill", "codexbar-legacy", sid, ledger.iso(now_utc))
            stats = ledger.apply_backfill(
                conn, series, rows, settings=settings, now_utc=now_utc, capture_id=cid, origin=ORIGIN
            )
            repo.finish_capture(conn, cid, ledger.iso(now_utc), status="ok", tool_version=plan["folder"], stats=stats.as_dict())
        plan[provider]["applied"] = stats.as_dict()
    return plan


def _overlay(base: list[DayUsage], rows: list[DayUsage]) -> list[DayUsage]:
    merged = {(r.day, r.model): r for r in base}
    merged.update({(r.day, r.model): r for r in rows})
    return list(merged.values())


def _by_month(rows: list[DayUsage]) -> dict[str, dict]:
    out: dict[str, dict] = defaultdict(lambda: {"tokens": 0, "cost_usd": 0.0, "days": 0})
    for r in rows:
        if r.model != DAY_TOTAL:
            continue
        m = out[r.day[:7]]
        m["tokens"] += r.total
        m["cost_usd"] = round(m["cost_usd"] + (r.cost_micros or 0) / 1e6, 2)
        m["days"] += 1
    return dict(sorted(out.items()))
