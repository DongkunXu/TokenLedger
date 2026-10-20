"""Codex usage read directly from this machine's session logs (``~/.codex``).

This replaces CodexBar for Codex. CodexBar counts the token total a forked
session inherits from its parent as new usage on the day of the fork (on
2026-10-04 it reported 542M where the logs hold 68M). Here every API request is
counted once:

* Newer logs write one ``token_usage_record`` per response. Files that have
  them use only them, deduplicated across all files by ``response_id``.
* Older files fall back to ``token_count`` events: the change in the session's
  running total between events. The first event of a file counts only its own
  ``last_token_usage``, so a total inherited from a parent session is never
  counted. These events are deduplicated across files by timestamp and running
  total, which also catches history copied from one file into another.

Requests are bucketed into days in the ledger timezone and priced one by one
with CodexBar's catalog and its v0.78.0 rules (``pricing.codex_request_cost_usd``),
given each request's time and whether its session was created by a ChatGPT
account (the file's first ``session_meta``; Auto-review is then free). The
archived raw output is the per-day, per-model summary this module builds;
``parse_logs_export`` turns it into ledger rows, both during a capture and when
a revision is accepted.
"""

from __future__ import annotations

import io
import json
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import IO, Iterator
from zoneinfo import ZoneInfo

import zstandard

from .. import homes
from ..catalog import CatalogError
from ..ledger import local_today
from ..models import DAY_TOTAL, CaptureResult, CostQuality, DayUsage, Scope, SeriesKey
from ..pricing import Catalog, codex_request_cost_usd, load_catalog
from .base import SourceContext, SourceError
FORMAT = "tokenledger-codex-logs/1"
FIELDS = ("input", "cached", "output", "reasoning")


@dataclass(frozen=True)
class Request:
    at: datetime
    model: str
    input: int  # includes cached input, as Codex reports it
    cached: int
    output: int  # includes reasoning
    reasoning: int
    chatgpt_account: bool = False  # the session was created by a ChatGPT account


class CodexLogsSource:
    id = "codex-logs"

    def __init__(self, home: Path | list[Path] | None = None, catalog_path: Path | None = None) -> None:
        """``home``: one Codex home or several; by default every one on this computer
        (``homes.codex_homes``). ``catalog_path``: a fixed price catalog file (tests)."""
        self.homes = [home] if isinstance(home, Path) else home
        self.catalog_path = catalog_path

    def collect(self, ctx: SourceContext) -> list[CaptureResult]:
        found = self.homes if self.homes is not None else homes.codex_homes(wsl=ctx.settings.wsl)
        files = sorted({f for home in found for f in log_files(home)})
        if not files:
            return []
        catalog = self._catalog()
        requests, skipped = read_requests(files)
        export = build_export(requests, catalog, ctx.settings.tz)
        export.update(files=len(files), skipped_lines=skipped)
        raw = json.dumps(export, indent=1, sort_keys=True).encode()
        return [parse_logs_export(raw, ctx=ctx)]


    def _catalog(self) -> Catalog:
        if self.catalog_path is not None:
            if not self.catalog_path.exists():
                raise SourceError(f"price catalog not found at {self.catalog_path}")
            return Catalog.from_models_dev(self.catalog_path.read_bytes())
        try:
            return load_catalog()
        except CatalogError as exc:
            raise SourceError(str(exc)) from exc


def log_files(home: Path) -> list[Path]:
    """Session logs, plain or zstd-compressed: newer Codex versions can compress logs untouched
    for a week into ``.jsonl.zst`` and delete the original."""
    patterns = ("sessions/**/*.jsonl", "sessions/**/*.jsonl.zst", "archived_sessions/*.jsonl", "archived_sessions/*.jsonl.zst")
    return sorted({p for pattern in patterns for p in home.glob(pattern)})


@contextmanager
def open_log(path: Path) -> Iterator[IO[bytes]]:
    """A log file as a binary stream of lines, decompressing ``.zst`` files on the fly."""
    with path.open("rb") as raw:
        if path.name.endswith(".zst"):
            with io.BufferedReader(zstandard.ZstdDecompressor().stream_reader(raw)) as fh:
                yield fh
        else:
            yield raw


def log_lines(path: Path) -> Iterator[bytes]:
    """The lines of a log; a damaged or unfinished compressed file yields what it can."""
    with open_log(path) as fh:
        try:
            yield from fh
        except zstandard.ZstdError:
            return


def read_requests(files: list[Path]) -> tuple[list[Request], int]:
    """Every request in the logs, once. Returns the requests and the number of unreadable lines."""
    seen: set[tuple] = set()
    out: list[Request] = []
    skipped = 0
    for path in files:
        events, bad = _file_events(path)
        skipped += bad
        for key, request in events:
            if key not in seen:
                seen.add(key)
                out.append(request)
    return out, skipped


def _file_events(path: Path) -> tuple[list[tuple[tuple, Request]], int]:
    model = "unknown"
    chatgpt_account: bool | None = None  # from the file's own (first) session_meta
    turn_models: dict[str, str] = {}
    records: list[tuple[tuple, datetime, str | None, dict]] = []
    counts: list[tuple[tuple, Request]] = []
    running: dict | None = None
    bad = 0
    for line in log_lines(path):
        # Most lines are conversation content; only four kinds matter here.
        if (
            b'"token_usage_record"' not in line
            and b'"token_count"' not in line
            and b'"turn_context"' not in line
            and (chatgpt_account is not None or b'"session_meta"' not in line)
        ):
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            bad += 1  # usually a line Codex is still writing
            continue
        kind = entry.get("type")
        payload = entry.get("payload") or {}
        if kind == "session_meta":
            if chatgpt_account is None:
                creator = payload.get("creator_account_id")
                chatgpt_account = isinstance(creator, str) and bool(creator.strip())
        elif kind == "turn_context":
            model = payload.get("model") or model
            if payload.get("turn_id"):
                turn_models[payload["turn_id"]] = model
        elif kind == "token_usage_record":
            key = ("response", payload.get("response_id")) if payload.get("response_id") else (
                "thread", payload.get("thread_id"), entry.get("timestamp"),
                (payload.get("thread_token_usage") or {}).get("total_tokens"),
            )
            records.append((key, _time(entry), payload.get("turn_id"), payload.get("usage") or {}))
        elif kind == "event_msg" and payload.get("type") == "token_count" and payload.get("info"):
            info = payload["info"]
            total = info.get("total_token_usage") or {}
            last = info.get("last_token_usage") or {}
            usage = last if running is None else {k: total.get(k, 0) - running.get(k, 0) for k in total}
            if any(v < 0 for v in usage.values() if isinstance(v, int)):
                usage = last  # the running total restarted
            running = total
            request = _request(_time(entry), model, usage)
            if request.input or request.output:
                counts.append((("count", entry.get("timestamp"), total.get("total_tokens")), request))
    events = [(key, _request(at, turn_models.get(turn, model), usage)) for key, at, turn, usage in records] if records else counts
    if chatgpt_account:
        events = [(key, replace(r, chatgpt_account=True)) for key, r in events]
    return events, bad


def _time(entry: dict) -> datetime:
    stamp = entry.get("timestamp")
    if not isinstance(stamp, str):
        raise SourceError(f"log entry without timestamp: {str(entry)[:120]}")
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def _request(at: datetime, model: str, usage: dict) -> Request:
    def n(key: str) -> int:
        value = usage.get(key) or 0
        if not isinstance(value, int) or value < 0:
            raise SourceError(f"token field {key} has unexpected value {value!r}")
        return value

    return Request(at, model, n("input_tokens"), n("cached_input_tokens"), n("output_tokens"), n("reasoning_output_tokens"))


def build_export(requests: list[Request], catalog: Catalog, tz: ZoneInfo) -> dict:
    """Sum requests per local day and model; cost is summed per request, in micro-dollars."""
    days: dict[str, dict[str, dict]] = defaultdict(dict)
    for r in requests:
        day = r.at.astimezone(tz).date().isoformat()
        m = days[day].setdefault(r.model, {"requests": 0, **{f: 0 for f in FIELDS}, "cost_micros": 0, "unpriced": 0})
        m["requests"] += 1
        for f in FIELDS:
            m[f] += getattr(r, f)
        usd = codex_request_cost_usd(
            catalog, r.model, r.input, r.cached, r.output, at=r.at, chatgpt_account=r.chatgpt_account
        )
        if usd is None:
            m["unpriced"] += 1
        else:
            m["cost_micros"] += round(usd * 1_000_000)
    return {
        "format": FORMAT,
        "timezone": str(tz),
        "catalog_fetched_at": catalog.fetched_at,
        "requests": len(requests),
        "days": {d: days[d] for d in sorted(days)},
    }


def parse_logs_export(raw: bytes, *, ctx: SourceContext) -> CaptureResult:
    export = json.loads(raw)
    if export.get("format") != FORMAT:
        raise SourceError(f"codex-logs: unexpected export format {export.get('format')!r}")
    if export.get("timezone") != ctx.settings.timezone:
        raise SourceError(f"codex-logs: export bucketed in {export.get('timezone')}, ledger uses {ctx.settings.timezone}")
    rows: list[DayUsage] = []
    for day, models in export["days"].items():
        rows.extend(_day_rows(day, models))
    today = local_today(ctx.settings, ctx.now_utc)
    days = sorted(export["days"])
    return CaptureResult(
        series=SeriesKey("codex-logs", "codex", ctx.settings.machine.id, Scope.MACHINE),
        rows=rows,
        window_start=days[0] if days else today.isoformat(),
        window_end=today.isoformat(),
        tool_version=f"catalog {export.get('catalog_fetched_at')}",
        raw_name="codex-logs",
        raw=raw,
        meta={k: export[k] for k in ("files", "requests", "skipped_lines") if k in export},
    )


def _day_rows(day: str, models: dict[str, dict]) -> list[DayUsage]:
    def usage(model: str, m: dict) -> DayUsage:
        # A day that mixes priced and unpriced models keeps the priced part as its
        # list-price figure; the model rows show which part is unpriced.
        priced = m["requests"] > m["unpriced"]
        return DayUsage(
            day=day,
            model=model,
            total=m["input"] + m["output"],
            input_uncached=m["input"] - m["cached"],
            cache_read=m["cached"],
            output=m["output"],
            reasoning=m["reasoning"],
            cost_micros=m["cost_micros"] if priced else None,
            cost_quality=CostQuality.LIST_PRICE if priced else CostQuality.UNPRICED,
        )

    total = {k: sum(m[k] for m in models.values()) for k in ("requests", *FIELDS, "cost_micros", "unpriced")}
    return [usage(DAY_TOTAL, total), *(usage(name, m) for name, m in sorted(models.items()))]
