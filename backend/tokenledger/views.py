"""Read-side queries shared by the HTTP API and the MCP server.

Combination rules for the per-provider daily view ("best" figures):

* tokens: the account-scope series when it has the day (the provider's own
  count across all devices), otherwise the sum of machine-scope series
* cost: always the sum of machine-scope series; account series carry no cost
* ``cost_tokens``: how many of the day's tokens the cost figure covers, so the
  UI can show when a cost is partial
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .config import Settings
from .models import DAY_TOTAL

PART_FIELDS = ("input_uncached", "cache_read", "cache_write", "output", "reasoning")


def _ledger_totals(conn: sqlite3.Connection, start: str, end: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT s.provider, s.scope, s.source, s.machine, l.* FROM ledger l JOIN series s ON s.id = l.series_id "
        "WHERE l.model = ? AND l.day BETWEEN ? AND ? ORDER BY l.day",
        (DAY_TOTAL, start, end),
    ).fetchall()


def bounds(conn: sqlite3.Connection) -> tuple[str, str] | None:
    row = conn.execute("SELECT MIN(day) AS lo, MAX(day) AS hi FROM ledger").fetchone()
    return (row["lo"], row["hi"]) if row["lo"] else None


def daily(conn: sqlite3.Connection, start: str, end: str) -> dict:
    """Per provider, per day: best tokens, local detail, account tokens and cost."""
    cells: dict[tuple[str, str], dict] = defaultdict(
        lambda: {
            "machine_tokens": 0,
            "machine_seen": False,
            "account_tokens": None,
            "cost_micros": 0,
            "cost_tokens": 0,
            "qualities": set(),
            "states": set(),
            **{f: 0 for f in PART_FIELDS},
        }
    )
    providers: set[str] = set()
    for r in _ledger_totals(conn, start, end):
        providers.add(r["provider"])
        c = cells[(r["provider"], r["day"])]
        c["states"].add(r["state"])
        if r["scope"] == "account":
            c["account_tokens"] = (c["account_tokens"] or 0) + r["total"]
            continue
        c["machine_seen"] = True
        c["machine_tokens"] += r["total"]
        for f in PART_FIELDS:
            c[f] += r[f] or 0
        if r["cost_micros"] is not None:
            c["cost_micros"] += r["cost_micros"]
            c["cost_tokens"] += r["total"]
        c["qualities"].add(r["cost_quality"])

    days = _day_list(start, end)
    out = []
    for provider in sorted(providers):
        rows = []
        for day in days:
            c = cells.get((provider, day))
            if c is None:
                rows.append(None)
                continue
            best = c["account_tokens"] if c["account_tokens"] is not None else c["machine_tokens"]
            rows.append(
                {
                    "tokens": best,
                    "basis": "account" if c["account_tokens"] is not None else "machine",
                    "machine_tokens": c["machine_tokens"] if c["machine_seen"] else None,
                    "account_tokens": c["account_tokens"],
                    "cost_usd": c["cost_micros"] / 1e6 if c["cost_tokens"] else None,
                    "cost_tokens": c["cost_tokens"],
                    "cost_quality": _worst_quality(c["qualities"]),
                    "open": "open" in c["states"],
                    "parts": {f: c[f] for f in PART_FIELDS} if c["machine_seen"] else None,
                }
            )
        out.append({"provider": provider, "values": rows})
    return {"start": start, "end": end, "days": days, "providers": out}


def summary(conn: sqlite3.Connection, start: str, end: str) -> dict:
    d = daily(conn, start, end)
    per_provider = {}
    all_by_day: dict[str, int] = defaultdict(int)
    for p in d["providers"]:
        tokens = cost = cost_tokens = 0
        active = 0
        parts: dict[str, int] = defaultdict(int)
        for day, v in zip(d["days"], p["values"]):
            if not v:
                continue
            tokens += v["tokens"]
            all_by_day[day] += v["tokens"]
            active += v["tokens"] > 0
            if v["cost_usd"] is not None:
                cost += v["cost_usd"]
                cost_tokens += v["cost_tokens"]
            for f, n in (v["parts"] or {}).items():
                parts[f] += n
        per_provider[p["provider"]] = {
            "tokens": tokens,
            "cost_usd": round(cost, 2),
            "cost_coverage": (cost_tokens / tokens) if tokens else None,
            "active_days": active,
            "parts": dict(parts),
        }
    active_days = sorted(day for day, t in all_by_day.items() if t > 0)
    peak = max(all_by_day.items(), key=lambda kv: kv[1], default=(None, 0))
    return {
        "start": start,
        "end": end,
        "tokens": sum(v["tokens"] for v in per_provider.values()),
        "cost_usd": round(sum(v["cost_usd"] for v in per_provider.values()), 2),
        "active_days": len(active_days),
        "peak_day": {"day": peak[0], "tokens": peak[1]},
        "current_streak": _streak(active_days, end),
        "providers": per_provider,
    }


def models(conn: sqlite3.Connection, start: str, end: str) -> list[dict]:
    rows = conn.execute(
        "SELECT s.provider, l.model, SUM(l.total) AS tokens, SUM(l.cost_micros) AS cost_micros, "
        "COUNT(DISTINCT l.day) AS days FROM ledger l JOIN series s ON s.id = l.series_id "
        "WHERE s.scope = 'machine' AND l.model != ? AND l.day BETWEEN ? AND ? "
        "GROUP BY s.provider, l.model ORDER BY tokens DESC",
        (DAY_TOTAL, start, end),
    ).fetchall()
    return [
        {
            "provider": r["provider"],
            "model": r["model"],
            "tokens": r["tokens"],
            "cost_usd": r["cost_micros"] / 1e6 if r["cost_micros"] is not None else None,
            "days": r["days"],
        }
        for r in rows
    ]


def quota(conn: sqlite3.Connection, start: str, end: str, max_points: int = 600) -> list[dict]:
    """Used-percent samples per provider and window, thinned evenly to at most ``max_points`` each.

    Each point is ``[captured_at, used_percent, resets_at]``.
    """
    rows = conn.execute(
        "SELECT provider, window, window_minutes, captured_at, used_percent, resets_at FROM quota_samples "
        "WHERE substr(captured_at, 1, 10) BETWEEN ? AND ? ORDER BY provider, window, captured_at",
        (start, end),
    ).fetchall()
    groups: dict[tuple[str, str], list] = defaultdict(list)
    minutes: dict[tuple[str, str], int | None] = {}
    for r in rows:
        key = (r["provider"], r["window"])
        groups[key].append([r["captured_at"], r["used_percent"], r["resets_at"]])
        minutes[key] = r["window_minutes"]
    out = []
    for (provider, window), points in sorted(groups.items()):
        step = max(1, len(points) // max_points)
        out.append(
            {"provider": provider, "window": window, "window_minutes": minutes[(provider, window)], "points": points[::step]}
        )
    return out


def quota_peaks(conn: sqlite3.Connection, start: str, end: str, tz: ZoneInfo) -> dict:
    """How much of each limit window was used at its highest point.

    * weekly: one point per weekly window, keyed by its reset time (hour-rounded, since
      reported reset times jitter by a few minutes). Windows that never rose above 0% are
      left out: Codex's weekly window is rolling, and while idle every sample reports a
      fresh "resets in 7 days", which would otherwise show up as many empty weeks.
    * session: the highest 5-hour-window usage on each local day
    Accounts of the same provider are merged by taking the higher value.
    """
    rows = conn.execute(
        "SELECT provider, window, captured_at, used_percent, resets_at FROM quota_samples "
        "WHERE window IN ('weekly', 'session') AND substr(captured_at, 1, 10) BETWEEN ? AND ?",
        (start, end),
    ).fetchall()
    weekly: dict[str, dict[str, float]] = defaultdict(dict)
    session: dict[str, dict[str, float]] = defaultdict(dict)
    for r in rows:
        used = float(r["used_percent"])
        if r["window"] == "weekly":
            if not r["resets_at"]:
                continue
            key = _hour(r["resets_at"])
            bucket = weekly[r["provider"]]
        else:
            key = datetime.fromisoformat(r["captured_at"].replace("Z", "+00:00")).astimezone(tz).date().isoformat()
            bucket = session[r["provider"]]
        bucket[key] = max(bucket.get(key, 0.0), used)
    return {
        "weekly": [
            {"provider": p, "points": sorted((k, used) for k, used in v.items() if used > 0)}
            for p, v in sorted(weekly.items())
        ],
        "session": [{"provider": p, "points": sorted(v.items())} for p, v in sorted(session.items())],
    }


def _hour(iso: str) -> str:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc)
    return (dt + timedelta(minutes=30)).replace(minute=0, second=0, microsecond=0).isoformat().replace("+00:00", "Z")


def revisions(conn: sqlite3.Connection, limit: int = 100) -> list[dict]:
    rows = conn.execute(
        "SELECT r.*, s.source, s.provider, s.scope FROM revisions r JOIN series s ON s.id = r.series_id "
        "ORDER BY r.id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def captures(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        "SELECT c.id, c.kind, c.source, s.provider, c.started_at, c.finished_at, c.status, c.tool_version, "
        "c.stats, c.error FROM captures c LEFT JOIN series s ON s.id = c.series_id ORDER BY c.id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def source_health(conn: sqlite3.Connection) -> list[dict]:
    """Current state of every source, judged by its latest attempt.

    One entry per source and provider. A failure recorded before any provider was known
    (for example the tool is missing) applies to that source's providers when it is newer
    than their own last attempt.
    """
    rows = conn.execute(
        "SELECT c.source, s.provider, c.started_at, c.status, c.error FROM captures c "
        "LEFT JOIN series s ON s.id = c.series_id WHERE c.kind IN ('usage', 'quota') ORDER BY c.id"
    ).fetchall()
    entries: dict[tuple[str, str | None], dict] = {}
    for r in rows:
        e = entries.setdefault(
            (r["source"], r["provider"]), {"source": r["source"], "provider": r["provider"], "last_ok_at": None}
        )
        ok = r["status"] == "ok"
        e.update(last_at=r["started_at"], ok=ok, error=None if ok else r["error"])
        if ok:
            e["last_ok_at"] = r["started_at"]

    out = []
    for (source, provider), e in entries.items():
        providers = [p for (s, p) in entries if s == source and p is not None]
        if provider is None and providers:
            continue  # folded into the provider entries below
        general = entries.get((source, None))
        if provider is not None and general and not general["ok"] and general["last_at"] > e["last_at"]:
            e = {**e, "ok": False, "last_at": general["last_at"], "error": general["error"]}
        out.append(e)
    return sorted(out, key=lambda e: (e["source"], e["provider"] or ""))


def status(conn: sqlite3.Connection, settings: Settings) -> dict:
    counts = conn.execute(
        "SELECT COUNT(*) AS rows, SUM(state='open') AS open, SUM(state='final') AS final FROM ledger"
    ).fetchone()
    b = bounds(conn)
    return {
        "machine": {"id": settings.machine.id, "label": settings.machine.label},
        "timezone": settings.timezone,
        "grace_days": settings.grace_days,
        "range": {"start": b[0], "end": b[1]} if b else None,
        "ledger_rows": counts["rows"],
        "open_rows": counts["open"] or 0,
        "final_rows": counts["final"] or 0,
        "revisions": conn.execute("SELECT COUNT(*) FROM revisions WHERE kind != 'replaced'").fetchone()[0],
        "replacements": conn.execute("SELECT COUNT(*) FROM revisions WHERE kind = 'replaced'").fetchone()[0],
        "quota_samples": conn.execute("SELECT COUNT(*) FROM quota_samples").fetchone()[0],
        "sources": source_health(conn),
    }


QUALITY_ORDER = ("none", "unpriced", "recomputed", "list_price")


def _worst_quality(qualities: set[str]) -> str | None:
    for q in QUALITY_ORDER:
        if q in qualities:
            return q
    return None


def _day_list(start: str, end: str) -> list[str]:
    d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
    return [(d0 + timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]


def _streak(active_days: list[str], end: str) -> int:
    active = set(active_days)
    day = date.fromisoformat(end)
    if day.isoformat() not in active:
        day -= timedelta(days=1)  # today may simply not have started yet
    n = 0
    while day.isoformat() in active:
        n += 1
        day -= timedelta(days=1)
    return n
