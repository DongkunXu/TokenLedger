"""Claude Code usage read directly from its session logs, on any operating system.

CodexBar reads these logs on macOS. Windows has no CodexBar, so this source
reads them itself with CodexBar v0.78.0's rules (``CostUsageScanner+Claude.swift``),
so that a day means the same on every machine:

* every ``*.jsonl`` below every Claude log folder on this computer
  (``homes.claude_log_roots``), in sorted order, skipping dot-named entries and
  anything that is not a regular file; subagent transcripts
  (``<session>/subagents/``) are included
* an API request is a line containing ``"type":"assistant"`` and ``"usage"``, at
  most 512 KiB long, that decodes the way Apple's JSONSerialization decodes it (a
  trailing comma is accepted; NaN, infinite numbers and unpaired surrogates reject
  the line; the first of two equal keys wins), with ``message.model``,
  ``message.usage`` and an RFC 3339 timestamp from 1900 on (``parse_timestamp``).
  Entries whose four token counts are all zero (Claude Code's ``<synthetic>``
  placeholders, API errors) and Vertex AI entries are skipped
* Claude Code writes one entry per content block of a response, so requests are
  counted once by ``message.id`` + ``requestId`` (without a ``requestId``:
  session id + ``message.id``) across all files and folders. Within a file the
  last entry wins; between files a main-session copy beats a subagent copy,
  then the first path in sorted order. A session copied into another folder, or
  pulled down from a cloud session with ``--teleport``, is counted once
* a streaming placeholder (null stop reason, input but no output, no cache
  fields) holds its request id but adds no tokens
* each request goes to the day of its timestamp in the ledger timezone. The
  model id is normalized (``pricing.normalize_claude_model``); each request is
  priced on its own (``request_cost_usd``) and the costs are summed per day and
  model in CodexBar's order, rounded once. A model whose day has any unpriced
  request is unpriced for that day

The archived raw output is the per-day, per-model summary built here, which
``parse_logs_export`` turns into ledger rows, during a capture and when a
revision is accepted.
"""

from __future__ import annotations

import json
import math
import os
import re
import stat
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .. import homes
from ..catalog import CatalogError
from ..ledger import local_today
from ..models import DAY_TOTAL, CaptureResult, CostQuality, DayUsage, Scope, SeriesKey
from ..pricing import Catalog, claude_cost_usd, load_catalog, normalize_claude_model
from .base import SourceContext, SourceError

FORMAT = "tokenledger-claude-logs/1"
FIELDS = ("input", "cache_read", "cache_write", "cache_write_1h", "output")
MAX_LINE_BYTES = 512 * 1024
_TIMESTAMP = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]{1,9}))?(Z|([+-])([0-9]{2})(:?[0-9]{2})?)"
)
_SURROGATE = re.compile(rb"\\u[dD][89a-fA-F]|\xed[\xa0-\xbf]")  # an escaped or raw UTF-16 surrogate
_VERTEX_KEYS = {"provider", "platform", "backend", "api_provider", "apiprovider", "api_type", "apitype", "source", "vendor", "client"}


@dataclass(frozen=True)
class Request:
    at: datetime  # to the millisecond
    raw_model: str
    model: str  # normalized
    input: int  # uncached input
    cache_read: int
    cache_write: int  # all cache writes, 5-minute and 1-hour
    cache_write_1h: int
    output: int
    incomplete: bool  # streaming placeholder: counted once, no tokens
    key: tuple | None  # dedup key; None = counted every time
    sidechain: bool
    subagent_file: bool


class ClaudeLogsSource:
    id = "claude-logs"

    def __init__(self, roots: list[Path] | None = None, catalog: Catalog | None = None) -> None:
        self.roots = roots
        self.catalog = catalog

    def collect(self, ctx: SourceContext) -> list[CaptureResult]:
        roots = self.roots if self.roots is not None else homes.claude_log_roots(wsl=ctx.settings.wsl)
        files = log_files(roots)
        if not files:
            return []
        try:
            catalog = self.catalog or load_catalog()
        except CatalogError as exc:
            raise SourceError(str(exc)) from exc
        requests, stats = read_requests(files)
        export = build_export(requests, catalog, ctx.settings.tz)
        export.update(files=len(files), roots=len(roots), **stats)
        raw = json.dumps(export, indent=1, sort_keys=True).encode()
        return [parse_logs_export(raw, ctx=ctx)]


def log_files(roots: list[Path]) -> list[Path]:
    """Every non-empty regular ``.jsonl`` file under the roots (a root may be a single file),
    skipping dot-named folders and files below a root and not following symlinked folders."""
    found: set[Path] = set()
    for root in roots:
        if root.is_file():
            found.add(root)
            continue
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for name in filenames:
                if name.startswith(".") or not name.lower().endswith(".jsonl"):
                    continue
                path = Path(dirpath) / name
                try:
                    st = path.stat()
                except OSError:
                    continue
                if stat.S_ISREG(st.st_mode) and st.st_size > 0:
                    found.add(path)
    return sorted(found, key=lambda p: str(p))


def read_requests(files: list[Path]) -> tuple[list[Request], dict]:
    """Every request in the logs, once, in CodexBar's order: requests without a key in file order,
    then the rest by key. Returns the requests and counts of what was skipped."""
    stats = {"skipped_lines": 0, "duplicates": 0}
    unkeyed: list[Request] = []
    winners: dict[tuple, tuple[str, Request]] = {}
    for path in files:
        for request in _file_requests(path, stats):
            if request.key is None:
                unkeyed.append(request)
                continue
            current = winners.get(request.key)
            if current is None:
                winners[request.key] = (str(path), request)
            else:
                stats["duplicates"] += 1
                if _wins((str(path), request), current):
                    winners[request.key] = (str(path), request)
    return unkeyed + [winners[k][1] for k in sorted(winners)], stats


def _wins(new: tuple[str, Request], old: tuple[str, Request]) -> bool:
    """Which copy of the same request counts (CodexBar's ``claudeRowWins``)."""
    (new_path, n), (old_path, o) = new, old
    if n.incomplete != o.incomplete:
        return not n.incomplete
    if n.sidechain != o.sidechain:
        return o.sidechain
    if n.subagent_file != o.subagent_file:
        return o.subagent_file
    return new_path < old_path


def _file_requests(path: Path, stats: dict) -> list[Request]:
    """The requests of one file; for repeated entries of one request the last complete one wins."""
    subagent_file = "subagents" in path.parts
    keyed: dict[tuple, Request] = {}
    unkeyed: list[Request] = []
    try:
        fh = path.open("rb")
    except OSError:
        return []  # removed while scanning
    with fh:
        for line in fh:
            if len(line) - line.endswith(b"\n") > MAX_LINE_BYTES:
                stats["skipped_lines"] += 1
                continue
            if b'"type":"assistant"' not in line or b'"usage"' not in line:
                continue  # most lines are conversation content
            entry = json_object(line)
            if entry is None:
                stats["skipped_lines"] += 1  # usually a line Claude Code is still writing
                continue
            request = _request(entry, subagent_file)
            if request is None:
                continue
            if request.key is None:
                unkeyed.append(request)
            elif not request.incomplete or request.key not in keyed or keyed[request.key].incomplete:
                if request.key in keyed:
                    stats["duplicates"] += 1
                keyed[request.key] = request
            else:
                stats["duplicates"] += 1
    return unkeyed + list(keyed.values())


def json_object(line: bytes) -> dict | None:
    """One line decoded the way CodexBar's JSONSerialization decodes it, or None."""
    try:
        value = _loads(line)
    except (ValueError, RecursionError):
        try:
            value = _loads(_without_trailing_commas(line)) if b"," in line else None
        except (ValueError, RecursionError):
            return None
    if not isinstance(value, dict) or (_SURROGATE.search(line) and _has_surrogate(value)):
        return None
    return value


def _loads(raw: bytes):
    return json.loads(raw, object_pairs_hook=_first_key_wins, parse_constant=_reject, parse_float=_finite)


def _first_key_wins(pairs: list) -> dict:
    out: dict = {}
    for k, v in pairs:
        out.setdefault(k, v)
    return out


def _reject(name: str):
    raise ValueError(f"{name} is not JSON")


def _finite(text: str) -> float:
    value = float(text)
    if math.isinf(value):
        raise ValueError(f"{text} is out of range")
    return value


def _without_trailing_commas(line: bytes) -> bytes:
    """Drop each comma that follows a value and comes right before a closing bracket."""
    out = bytearray()
    in_string = escaped = False
    comma = None  # where in out a comma after a value sits
    last = None  # the last byte outside strings and whitespace
    for b in line:
        if in_string:
            out.append(b)
            if escaped:
                escaped = False
            elif b == 0x5C:
                escaped = True
            elif b == 0x22:
                in_string = False
            continue
        if b in b" \t\r\n":
            out.append(b)
            continue
        if b in b"}]" and comma is not None:
            del out[comma]
        comma = len(out) if b == 0x2C and last not in (None, 0x2C, 0x3A, 0x5B, 0x7B) else None
        in_string = b == 0x22
        out.append(b)
        last = b
    return bytes(out)


def _has_surrogate(value) -> bool:
    if isinstance(value, str):
        try:
            value.encode()
        except UnicodeEncodeError:
            return True
        return False
    if isinstance(value, dict):
        return any(_has_surrogate(k) or _has_surrogate(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_has_surrogate(v) for v in value)
    return False


def parse_timestamp(text: str) -> datetime | None:
    """``claudeTimestampAndDayKey``: ``YYYY-MM-DDTHH:MM:SS[.fraction]`` with ``Z`` or a ``+HH:MM``
    offset (also ``+HHMM`` and ``+HH``), a real date from 1900 on, up to nine fraction digits
    of which the first three count."""
    m = _TIMESTAMP.fullmatch(text)
    if m is None:
        return None
    year, month, day, hour, minute, second = (int(g) for g in m.group(1, 2, 3, 4, 5, 6))
    if year < 1900:
        return None
    if m.group(8) == "Z":
        zone = timezone.utc
    else:
        hours, minutes = int(m.group(10)), int((m.group(11) or "0").lstrip(":"))
        if hours > 23 or minutes > 59:
            return None
        zone = timezone((-1 if m.group(9) == "-" else 1) * timedelta(hours=hours, minutes=minutes))
    millis = int((m.group(7) or "")[:3].ljust(3, "0"))
    try:
        return datetime(year, month, day, hour, minute, second, millis * 1000, tzinfo=zone)
    except ValueError:
        return None  # no such date or time


def _count(value) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return max(0, int(value))
    return 0


def _request(entry: dict, subagent_file: bool) -> Request | None:
    if not isinstance(entry, dict) or entry.get("type") != "assistant":
        return None
    message = entry.get("message") if isinstance(entry.get("message"), dict) else None
    if _is_vertex(entry, message):
        return None
    at = parse_timestamp(entry["timestamp"]) if isinstance(entry.get("timestamp"), str) else None
    if at is None or message is None:
        return None
    model, usage = message.get("model"), message.get("usage")
    if not isinstance(model, str) or not isinstance(usage, dict):
        return None
    inp = _count(usage.get("input_tokens"))
    cache_write = _count(usage.get("cache_creation_input_tokens"))
    cache_read = _count(usage.get("cache_read_input_tokens"))
    output = _count(usage.get("output_tokens"))
    if not (inp or cache_write or cache_read or output):
        return None
    detail = usage.get("cache_creation")
    one_hour = min(cache_write, _count(detail.get("ephemeral_1h_input_tokens"))) if isinstance(detail, dict) else 0
    incomplete = (
        "stop_reason" in message
        and message["stop_reason"] is None
        and inp > 0
        and output == 0
        and "cache_read_input_tokens" not in usage
        and "cache_creation_input_tokens" not in usage
    )
    sidechain = entry.get("isSidechain")
    return Request(
        at=at,
        raw_model=model,
        model=normalize_claude_model(model),
        input=inp,
        cache_read=cache_read,
        cache_write=cache_write,
        cache_write_1h=one_hour,
        output=output,
        incomplete=incomplete,
        key=_key(entry, message),
        sidechain=bool(sidechain) if isinstance(sidechain, (bool, int, float)) else False,
        subagent_file=subagent_file,
    )


def _key(entry: dict, message: dict) -> tuple | None:
    message_id = message.get("id") if isinstance(message.get("id"), str) else None
    if message_id is None:
        return None
    request_id = entry.get("requestId")
    if isinstance(request_id, str):
        return ("request", message_id, request_id)
    session = next(
        (
            s
            for s in (
                entry.get("sessionId"),
                entry.get("session_id"),
                (entry.get("metadata") or {}).get("sessionId") if isinstance(entry.get("metadata"), dict) else None,
                (message.get("metadata") or {}).get("sessionId") if isinstance(message.get("metadata"), dict) else None,
            )
            if isinstance(s, str)
        ),
        None,
    )
    if not message_id.strip() or not (session or "").strip():
        return None
    return ("session", session, message_id)


def _is_vertex(entry: dict, message: dict | None) -> bool:
    """Vertex AI usage is billed by Google, not Anthropic; CodexBar leaves it out of Claude."""
    if message and isinstance(message.get("id"), str) and "_vrtx_" in message["id"]:
        return True
    if isinstance(entry.get("requestId"), str) and "_vrtx_" in entry["requestId"]:
        return True
    model = (message or {}).get("model")
    if isinstance(model, str) and model.startswith("claude-") and "@" in model:
        return True
    return _vertex_walk(entry)


def _vertex_walk(d: dict) -> bool:
    for k, v in d.items():
        kl = k.lower()
        if "vertex" in kl or "gcp" in kl:
            return True
        if kl in _VERTEX_KEYS and isinstance(v, str) and "vertex" in v.lower():
            return True
        if isinstance(v, dict) and _vertex_walk(v):
            return True
        if isinstance(v, list) and any(isinstance(e, dict) and _vertex_walk(e) for e in v):
            return True
    return False


def build_export(requests: list[Request], catalog: Catalog, tz: ZoneInfo) -> dict:
    """Sum requests per local day and model. Cost is summed in dollars and rounded once."""
    days: dict[str, dict[str, dict]] = defaultdict(dict)
    for r in requests:
        day = r.at.astimezone(tz).date().isoformat()
        m = days[day].setdefault(
            r.model, {"requests": 0, "incomplete": 0, **{f: 0 for f in FIELDS}, "cost_usd": 0.0, "unpriced": 0}
        )
        if r.incomplete:
            m["incomplete"] += 1
            continue
        m["requests"] += 1
        for f in FIELDS:
            m[f] += getattr(r, f)
        usd = request_cost_usd(catalog, r)
        if usd is None:
            m["unpriced"] += 1
        else:
            m["cost_usd"] += usd
    return {
        "format": FORMAT,
        "timezone": str(tz),
        "catalog_fetched_at": catalog.fetched_at,
        "requests": len(requests),
        "days": {d: days[d] for d in sorted(days)},
    }


def request_cost_usd(catalog: Catalog, r: Request) -> float | None:
    """What CodexBar's report charges for one request. It prices the logged model id while
    scanning (kept in billionths of a dollar) and the normalized id while reporting: a scan
    price that rounds to zero stays zero, else the report price, else the scan price."""

    def cost(model: str) -> float | None:
        return claude_cost_usd(
            catalog,
            model,
            input_tokens=r.input,
            cache_read=r.cache_read,
            cache_write=r.cache_write,
            cache_write_1h=r.cache_write_1h,
            output=r.output,
            at=r.at,
        )

    scanned = cost(r.raw_model)
    nanos = math.floor(scanned * 1e9 + 0.5) if scanned is not None and scanned * 1e9 < 2**63 else None
    if nanos == 0:
        return 0.0
    reported = scanned if r.raw_model == r.model else cost(r.model)
    if reported is not None:
        return reported
    return nanos / 1e9 if nanos is not None else None


def parse_logs_export(raw: bytes, *, ctx: SourceContext) -> CaptureResult:
    export = json.loads(raw)
    if export.get("format") != FORMAT:
        raise SourceError(f"claude-logs: unexpected export format {export.get('format')!r}")
    if export.get("timezone") != ctx.settings.timezone:
        raise SourceError(f"claude-logs: export bucketed in {export.get('timezone')}, ledger uses {ctx.settings.timezone}")
    rows: list[DayUsage] = []
    for day, models in export["days"].items():
        rows.extend(_day_rows(day, models))
    today = local_today(ctx.settings, ctx.now_utc)
    days = sorted(export["days"])
    return CaptureResult(
        series=SeriesKey("claude-logs", "claude", ctx.settings.machine.id, Scope.MACHINE),
        rows=rows,
        window_start=days[0] if days else today.isoformat(),
        window_end=today.isoformat(),
        tool_version=f"catalog {export.get('catalog_fetched_at')}",
        raw_name="claude-logs",
        raw=raw,
        meta={k: export[k] for k in ("files", "requests", "skipped_lines") if k in export},
    )


def _day_rows(day: str, models: dict[str, dict]) -> list[DayUsage]:
    def usage(model: str, m: dict, cost_usd: float | None) -> DayUsage:
        return DayUsage(
            day=day,
            model=model,
            total=m["input"] + m["cache_read"] + m["cache_write"] + m["output"],
            input_uncached=m["input"],
            cache_read=m["cache_read"],
            cache_write=m["cache_write"],
            output=m["output"],
            cost_micros=round(cost_usd * 1_000_000) if cost_usd is not None else None,
            cost_quality=CostQuality.LIST_PRICE if cost_usd is not None else CostQuality.UNPRICED,
        )

    def priced(m: dict) -> bool:
        return m["requests"] > 0 and m["unpriced"] == 0

    ordered = sorted(models.items())  # CodexBar adds the models of a day in name order
    rows = [usage(name, m, m["cost_usd"] if priced(m) else None) for name, m in ordered]
    total = {f: sum(m[f] for _, m in ordered) for f in FIELDS}
    priced_models = [m for _, m in ordered if priced(m)]
    day_cost = sum(m["cost_usd"] for m in priced_models) if priced_models else None
    return [usage(DAY_TOTAL, total, day_cost), *rows]
