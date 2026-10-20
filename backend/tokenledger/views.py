"""Read-side queries shared by the HTTP API and the MCP server.

Combination rules for the per-provider daily view ("best" figures):

* tokens: the account-scope series when it has the day (the provider's own
  count across all devices), otherwise the sum of machine-scope series. While
  a day is still open the larger of the two is used: the provider's count
  arrives half a day to a day late, and until then this machine's logs are the
  better lower bound. Final days keep the account figure, whose day boundary
  (UTC for Codex) differs from the ledger's, so mixing the two there would
  count usage near midnight twice.
* cost: always the sum of machine-scope series; account series carry no cost
* ``cost_tokens``: how many of the day's tokens the cost figure covers, so the
  UI can show when a cost is partial
* machine figures: when several sources read the same provider on the same
  machine, each day comes from the first source in ``MACHINE_SOURCE_ORDER`` that
  has it. Codex moved from CodexBar to codex-logs; CodexBar's rows remain as the
  record of days whose logs are gone. Claude comes from CodexBar where it runs and
  from claude-logs elsewhere
* several machines: account figures are added up across accounts (each account
  once, whichever machine read it). Each machine's own logs belong to the account
  it reads, so they count through that account's figure; the logs of a machine
  that reads no account of a provider are added on top. ``machine_usage`` splits
  the machine figures by machine and shows the account usage no machine in the
  ledger accounts for (other devices, cloud tasks)
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Callable
from zoneinfo import ZoneInfo

from .config import Settings
from .models import DAY_TOTAL

PART_FIELDS = ("input_uncached", "cache_read", "cache_write", "output", "reasoning")
# Codex: its own logs before CodexBar's record of them. Claude: CodexBar on a Mac, the
# claude-logs source (same rules) on machines without it.
MACHINE_SOURCE_ORDER = ("codex-logs", "codexbar", "claude-logs")
RETIRED_SOURCES = {("codexbar", "codex")}  # no longer captured; kept out of the health list


def _mine(only: str | None) -> tuple[str, tuple]:
    """SQL condition on ``series s`` for the series ``only`` (this machine) holds itself: its
    own and the account series it read. Without ``only``, every series in the ledger."""
    if only is None:
        return "1", ()
    own = "SELECT series_id FROM captures WHERE machine IS NULL AND series_id IS NOT NULL"
    return f"(s.machine = ? OR s.id IN ({own}))", (only,)


def _ledger_totals(conn: sqlite3.Connection, start: str, end: str, only: str | None = None) -> list[sqlite3.Row]:
    """Day-total rows of every account series and of the preferred machine series."""
    mine, args = _mine(only)
    rows = conn.execute(
        "SELECT s.provider, s.scope, s.source, s.machine, l.* FROM ledger l JOIN series s ON s.id = l.series_id "
        f"WHERE l.model = ? AND l.day BETWEEN ? AND ? AND {mine} ORDER BY l.day",
        (DAY_TOTAL, start, end, *args),
    ).fetchall()
    chosen = _preferred(rows)
    return [r for r in rows if r["scope"] == "account" or (r["series_id"], r["day"]) in chosen]


def _preferred(totals: list[sqlite3.Row]) -> set[tuple[int, str]]:
    """(series, day) pairs to read machine figures from: per provider, machine and day,
    the first source in MACHINE_SOURCE_ORDER that has the day."""

    def rank(source: str) -> int:
        return MACHINE_SOURCE_ORDER.index(source) if source in MACHINE_SOURCE_ORDER else len(MACHINE_SOURCE_ORDER)

    best: dict[tuple[str, str, str], sqlite3.Row] = {}
    for r in totals:
        if r["scope"] != "machine":
            continue
        key = (r["provider"], r["machine"], r["day"])
        if key not in best or rank(r["source"]) < rank(best[key]["source"]):
            best[key] = r
    return {(r["series_id"], r["day"]) for r in best.values()}


def bounds(conn: sqlite3.Connection, only: str | None = None) -> tuple[str, str] | None:
    mine, args = _mine(only)
    row = conn.execute(
        f"SELECT MIN(l.day) AS lo, MAX(l.day) AS hi FROM ledger l JOIN series s ON s.id = l.series_id WHERE {mine}", args
    ).fetchone()
    return (row["lo"], row["hi"]) if row["lo"] else None


def _coverage(conn: sqlite3.Connection) -> Callable[[str, str], bool]:
    """Whether a machine's logs of a provider are already counted by an account-wide
    series: they are when the machine reads that provider's account (each machine's own
    logs belong to the account it is signed in to). A machine that has not recorded which
    accounts it reads (a ledger from before sync) counts as reading them all."""
    recorded = {
        r["id"]: {provider for provider, _ in json.loads(r["accounts"])}
        for r in conn.execute("SELECT id, accounts FROM machines WHERE accounts IS NOT NULL")
    }
    return lambda provider, machine: machine not in recorded or provider in recorded[machine]


def _cells(conn: sqlite3.Connection, start: str, end: str, only: str | None = None) -> dict[tuple[str, str], dict]:
    """Per provider and day: every figure the views need, before choosing between them."""
    cells: dict[tuple[str, str], dict] = defaultdict(
        lambda: {
            "covered_tokens": 0,  # machine figures of machines that read an account
            "uncovered_tokens": 0,  # machine figures of machines that read none
            "covered_seen": False,
            "machine_seen": False,
            "account_tokens": None,
            "by_machine": defaultdict(int),
            "cost_by_machine": defaultdict(int),
            "cost_micros": 0,
            "cost_tokens": 0,
            "qualities": set(),
            "states": set(),
            **{f: 0 for f in PART_FIELDS},
        }
    )
    covered = _coverage(conn)
    for r in _ledger_totals(conn, start, end, only):
        c = cells[(r["provider"], r["day"])]
        c["states"].add(r["state"])
        if r["scope"] == "account":
            c["account_tokens"] = (c["account_tokens"] or 0) + r["total"]
            continue
        c["machine_seen"] = True
        c["by_machine"][r["machine"]] += r["total"]
        if covered(r["provider"], r["machine"]):
            c["covered_tokens"] += r["total"]
            c["covered_seen"] = True
        else:
            c["uncovered_tokens"] += r["total"]
        for f in PART_FIELDS:
            c[f] += r[f] or 0
        if r["cost_micros"] is not None:
            c["cost_micros"] += r["cost_micros"]
            c["cost_tokens"] += r["total"]
            c["cost_by_machine"][r["machine"]] += r["cost_micros"]
        c["qualities"].add(r["cost_quality"])
    for c in cells.values():
        # The account figure is the provider's own count for every device signed in to it.
        # While a day is open it may still lag behind the machines that read it.
        c["account_chosen"] = c["account_tokens"] is not None and (
            "open" not in c["states"] or not c["covered_seen"] or c["account_tokens"] >= c["covered_tokens"]
        )
        counted = c["account_tokens"] if c["account_chosen"] else c["covered_tokens"]
        c["tokens"] = counted + c["uncovered_tokens"]
        # Account usage no machine in this ledger accounts for: other devices, cloud tasks. It
        # can be negative on a day: the account's day boundary (UTC for Codex) differs from the
        # machines', so usage near midnight lands on neighbouring days (see machine_usage).
        c["unattributed"] = c["account_tokens"] - c["covered_tokens"] if c["account_chosen"] else 0
    return cells


def daily(conn: sqlite3.Connection, start: str, end: str, only: str | None = None) -> dict:
    """Per provider, per day: best tokens, local detail, account tokens and cost."""
    cells = _cells(conn, start, end, only)
    providers = sorted({p for p, _ in cells})
    days = _day_list(start, end)
    out = []
    for provider in providers:
        rows = []
        for day in days:
            c = cells.get((provider, day))
            if c is None:
                rows.append(None)
                continue
            machine_tokens = c["covered_tokens"] + c["uncovered_tokens"]
            rows.append(
                {
                    "tokens": c["tokens"],
                    "basis": "account" if c["account_chosen"] else "machine",
                    "machine_tokens": machine_tokens if c["machine_seen"] else None,
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


def machine_usage(
    conn: sqlite3.Connection, start: str, end: str, local_machine: str | None = None, only: str | None = None
) -> dict:
    """Tokens per machine (each machine's own logs) and the account usage none of them
    accounts for, in total and per day. Machines and the rest add up to ``summary``'s total
    unless the machines' logs exceed their accounts' figures over the whole range."""
    cells = _cells(conn, start, end, only)
    days = _day_list(start, end)
    index = {d: i for i, d in enumerate(days)}
    labels = {r["id"]: dict(r) for r in conn.execute("SELECT id, label, os FROM machines")}
    per: dict[str, dict] = {}
    other = {"tokens": 0, "values": [0] * len(days), "providers": defaultdict(int)}
    cost: dict[str, int] = defaultdict(int)
    for (provider, day), c in cells.items():
        for machine, tokens in c["by_machine"].items():
            m = per.setdefault(machine, {"tokens": 0, "values": [0] * len(days), "providers": defaultdict(int)})
            m["tokens"] += tokens
            m["values"][index[day]] += tokens
            m["providers"][provider] += tokens
        for machine, micros in c["cost_by_machine"].items():
            cost[machine] += micros
        other["tokens"] += c["unattributed"]
        other["values"][index[day]] += max(0, c["unattributed"])
        other["providers"][provider] += c["unattributed"]
    machines = [
        {
            "id": machine,
            "label": (labels.get(machine) or {}).get("label", machine),
            "os": (labels.get(machine) or {}).get("os"),
            "this_machine": machine == local_machine,
            "tokens": m["tokens"],
            "cost_usd": round(cost[machine] / 1e6, 2) if machine in cost else None,
            "providers": dict(m["providers"]),
            "values": m["values"],
        }
        for machine, m in per.items()
    ]
    machines.sort(key=lambda m: m["tokens"], reverse=True)
    # Over the range the day-boundary shifts cancel out, so the machines and the rest add up
    # to the ledger's total; a single day's bar shows only what is above its machines.
    other["tokens"] = max(0, other["tokens"])
    other["providers"] = {p: n for p, n in other["providers"].items() if n > 0}
    return {"start": start, "end": end, "days": days, "machines": machines, "other": other}


def summary(conn: sqlite3.Connection, start: str, end: str, only: str | None = None) -> dict:
    d = daily(conn, start, end, only)
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


def models(conn: sqlite3.Connection, start: str, end: str, only: str | None = None) -> list[dict]:
    """Per provider and model, from the preferred machine series of each day."""
    mine, args = _mine(only)
    rows = conn.execute(
        "SELECT s.provider, s.scope, s.source, s.machine, l.series_id, l.day, l.model, l.total, l.cost_micros "
        f"FROM ledger l JOIN series s ON s.id = l.series_id WHERE s.scope = 'machine' AND l.day BETWEEN ? AND ? AND {mine}",
        (start, end, *args),
    ).fetchall()
    chosen = _preferred([r for r in rows if r["model"] == DAY_TOTAL])
    agg: dict[tuple[str, str], dict] = {}
    for r in rows:
        if r["model"] == DAY_TOTAL or (r["series_id"], r["day"]) not in chosen:
            continue
        a = agg.setdefault((r["provider"], r["model"]), {"tokens": 0, "cost_micros": None, "days": set()})
        a["tokens"] += r["total"]
        a["days"].add(r["day"])
        if r["cost_micros"] is not None:
            a["cost_micros"] = (a["cost_micros"] or 0) + r["cost_micros"]
    out = [
        {
            "provider": provider,
            "model": model,
            "tokens": a["tokens"],
            "cost_usd": a["cost_micros"] / 1e6 if a["cost_micros"] is not None else None,
            "days": len(a["days"]),
        }
        for (provider, model), a in agg.items()
    ]
    return sorted(out, key=lambda m: m["tokens"], reverse=True)


def _sampled(only: str | None) -> str:
    """SQL condition on quota samples: the ones this machine took itself, or all."""
    return "machine IS NULL" if only else "1"


def quota(conn: sqlite3.Connection, start: str, end: str, max_points: int = 600, only: str | None = None) -> list[dict]:
    """Used-percent samples per provider and window, thinned evenly to at most ``max_points`` each.

    Each point is ``[captured_at, used_percent, resets_at]``.
    """
    rows = conn.execute(
        "SELECT provider, window, window_minutes, captured_at, used_percent, resets_at FROM quota_samples "
        f"WHERE substr(captured_at, 1, 10) BETWEEN ? AND ? AND {_sampled(only)} ORDER BY provider, window, captured_at",
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


def quota_peaks(conn: sqlite3.Connection, start: str, end: str, tz: ZoneInfo, only: str | None = None) -> dict:
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
        f"WHERE window IN ('weekly', 'session') AND substr(captured_at, 1, 10) BETWEEN ? AND ? AND {_sampled(only)}",
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


def quota_now(conn: sqlite3.Connection, only: str | None = None) -> list[dict]:
    """The latest reading of every limit window, one entry per provider and window.

    Each account's newest sample is taken; when a provider has several accounts, the one
    using more of its limit is reported. ``resets_at`` may already be in the past when no
    capture has run since the window reset; readers treat that window as unused.
    """
    rows = conn.execute(
        "SELECT q.provider, q.window, q.window_minutes, q.captured_at, q.used_percent, q.resets_at "
        "FROM quota_samples q JOIN (SELECT provider, account, window, MAX(captured_at) AS at FROM quota_samples "
        f"WHERE {_sampled(only)} GROUP BY provider, account, window) l ON l.provider = q.provider "
        "AND l.account = q.account AND l.window = q.window AND l.at = q.captured_at"
    ).fetchall()
    best: dict[tuple[str, str], dict] = {}
    for r in rows:
        key = (r["provider"], r["window"])
        if key not in best or r["used_percent"] > best[key]["used_percent"]:
            best[key] = dict(r)
    return [best[k] for k in sorted(best)]


def _hour(iso: str) -> str:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc)
    return (dt + timedelta(minutes=30)).replace(minute=0, second=0, microsecond=0).isoformat().replace("+00:00", "Z")


def revisions(conn: sqlite3.Connection, limit: int = 100, only: str | None = None) -> list[dict]:
    mine, args = _mine(only)
    rows = conn.execute(
        "SELECT r.*, s.source, s.provider, s.scope, s.machine FROM revisions r JOIN series s ON s.id = r.series_id "
        f"WHERE {mine} ORDER BY r.id DESC LIMIT ?",
        (*args, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def captures(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        "SELECT c.id, c.kind, c.source, s.provider, c.machine, c.started_at, c.finished_at, c.status, c.tool_version, "
        "c.stats, c.error FROM captures c LEFT JOIN series s ON s.id = c.series_id ORDER BY c.started_at DESC, c.id DESC "
        "LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def source_health(conn: sqlite3.Connection, local_machine: str | None = None, only: str | None = None) -> list[dict]:
    """Current state of every source on every machine, judged by its latest attempt.

    One entry per machine, source and provider. A failure recorded before any provider
    was known (for example the tool is missing) applies to that source's providers on
    the same machine when it is newer than their own last attempt. This machine's own
    captures have no machine recorded; they are reported under ``local_machine``.
    """
    rows = conn.execute(
        "SELECT c.machine, c.source, s.provider, c.started_at, c.status, c.error FROM captures c "
        "LEFT JOIN series s ON s.id = c.series_id WHERE c.kind IN ('usage', 'quota') "
        f"{'AND c.machine IS NULL' if only else ''} ORDER BY c.started_at, c.id"
    ).fetchall()
    labels = {r["id"]: r["label"] for r in conn.execute("SELECT id, label FROM machines")}
    entries: dict[tuple[str | None, str, str | None], dict] = {}
    for r in rows:
        if (r["source"], r["provider"]) in RETIRED_SOURCES:
            continue
        machine = r["machine"] or local_machine
        e = entries.setdefault(
            (machine, r["source"], r["provider"]),
            {
                "machine": machine,
                "machine_label": labels.get(machine, machine),
                "source": r["source"],
                "provider": r["provider"],
                "last_ok_at": None,
            },
        )
        ok = r["status"] == "ok"
        e.update(last_at=r["started_at"], ok=ok, error=None if ok else r["error"])
        if ok:
            e["last_ok_at"] = r["started_at"]

    # A source that a machine no longer runs (removed tool, not available on its platform)
    # would keep its last result forever; it is left out once that machine's captures have
    # moved on by more than a day.
    newest: dict[str | None, str] = {}
    for (machine, _, _), e in entries.items():
        newest[machine] = max(newest.get(machine, ""), e["last_at"])
    out = []
    for (machine, source, provider), e in entries.items():
        if _days_between(e["last_at"], newest[machine]) > 1:
            continue
        providers = [p for (m, s, p) in entries if m == machine and s == source and p is not None]
        if provider is None and providers:
            continue  # folded into the provider entries below
        general = entries.get((machine, source, None))
        if provider is not None and general and not general["ok"] and general["last_at"] > e["last_at"]:
            e = {**e, "ok": False, "last_at": general["last_at"], "error": general["error"]}
        out.append(e)
    local_first = lambda e: (e["machine"] != local_machine, e["machine_label"] or "", e["source"], e["provider"] or "")  # noqa: E731
    return sorted(out, key=local_first)


def _days_between(a: str, b: str) -> float:
    try:
        da = datetime.fromisoformat(a.replace("Z", "+00:00"))
        db = datetime.fromisoformat(b.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if (da.tzinfo is None) != (db.tzinfo is None):
        da, db = da.replace(tzinfo=None), db.replace(tzinfo=None)
    return (db - da).total_seconds() / 86400


def machines(conn: sqlite3.Connection, local_machine: str | None = None, only: str | None = None) -> list[dict]:
    """Every machine with data in this ledger (only ``only``, when given); ``snapshot_at`` is
    when the data of another machine was taken there, ``synced_at`` when it arrived here."""
    rows = conn.execute(
        "SELECT m.id, m.label, m.os, m.first_seen_at, m.app_version, m.snapshot_at, m.synced_at, "
        "(SELECT MAX(l.day) FROM ledger l JOIN series s ON s.id = l.series_id WHERE s.machine = m.id) AS last_day "
        f"FROM machines m {'WHERE m.id = ?' if only else ''} ORDER BY m.id != ?, m.label",
        (*([only] if only else []), local_machine),
    ).fetchall()
    return [{**dict(r), "this_machine": r["id"] == local_machine} for r in rows]


def status(conn: sqlite3.Connection, settings: Settings, only: str | None = None) -> dict:
    mine, args = _mine(only)
    counts = conn.execute(
        "SELECT COUNT(*) AS rows, SUM(l.state='open') AS open, SUM(l.state='final') AS final "
        f"FROM ledger l JOIN series s ON s.id = l.series_id WHERE {mine}",
        args,
    ).fetchone()
    revs = conn.execute(
        "SELECT SUM(r.kind != 'replaced') AS changed, SUM(r.kind = 'replaced') AS replaced "
        f"FROM revisions r JOIN series s ON s.id = r.series_id WHERE {mine}",
        args,
    ).fetchone()
    b = bounds(conn, only)
    return {
        "machine": {"id": settings.machine.id, "label": settings.machine.label},
        "timezone": settings.timezone,
        "grace_days": settings.grace_days,
        "range": {"start": b[0], "end": b[1]} if b else None,
        "ledger_rows": counts["rows"],
        "open_rows": counts["open"] or 0,
        "final_rows": counts["final"] or 0,
        "revisions": revs["changed"] or 0,
        "replacements": revs["replaced"] or 0,
        "quota_samples": conn.execute(f"SELECT COUNT(*) FROM quota_samples WHERE {_sampled(only)}").fetchone()[0],
        "sources": source_health(conn, settings.machine.id, only),
        "machines": machines(conn, settings.machine.id, only),
    }


RANGE_DAYS = {"d30": 30, "d90": 90, "d180": 182, "y1": 365, "all": None}  # the dashboard's ranges


def span(conn: sqlite3.Connection, range_key: str, today: str, only: str | None = None) -> tuple[str, str]:
    """The days a dashboard range covers, ending today."""
    if range_key not in RANGE_DAYS:
        raise ValueError(f"unknown range {range_key!r}; one of {', '.join(RANGE_DAYS)}")
    days = RANGE_DAYS[range_key]
    if days is None:
        b = bounds(conn, only)
        return (min(b[0], today) if b else today), today
    return (date.fromisoformat(today) - timedelta(days=days - 1)).isoformat(), today


def dashboard(
    conn: sqlite3.Connection, settings: Settings, range_key: str, today: str, *, scope: str, version: str, sync: dict
) -> dict:
    """Everything one dashboard range shows, in one answer (``GET /api/view``).

    ``scope`` "machine": what this machine holds itself (its own logs, the accounts it
    reads), as its local dashboard shows it. "ledger": every machine in the ledger, as the
    cloud dashboard shows it. ``version`` and ``sync`` (this machine's sync status) go into
    the status as the dashboard shows them."""
    if scope not in ("machine", "ledger"):
        raise ValueError(f"scope must be machine or ledger, got {scope!r}")
    only = settings.machine.id if scope == "machine" else None
    start, end = span(conn, range_key, today, only)
    return {
        "range": range_key,
        "status": {**status(conn, settings, only), "today": today, "scope": scope, "version": version, "sync": sync},
        "summary": summary(conn, start, end, only),
        "daily": daily(conn, start, end, only),
        "models": models(conn, start, end, only),
        "machines": machine_usage(conn, start, end, settings.machine.id, only),
        "peaks": quota_peaks(conn, start, end, settings.tz, only),
        "quota": quota_now(conn, only),
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
