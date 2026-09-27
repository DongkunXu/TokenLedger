"""CodexBar ``cost`` export: daily tokens and list-price cost from this machine's logs.

Runs ``codexbar cost --provider <p> --days 365 --format json`` once per provider.
CodexBar reports Claude input without cache reads and Codex input including
them; both are normalized here (see ``DayUsage``).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import timedelta

from ..ledger import local_today
from ..models import DAY_TOTAL, CaptureResult, CostQuality, DayUsage, Scope, SeriesKey
from .base import SourceContext, SourceError, run_tool

HISTORY_DAYS = 365  # CodexBar's maximum
PROVIDERS = ("claude", "codex", "antigravity")
TIMEOUT_S = 900.0
DEFAULTS_DOMAIN = "com.steipete.codexbar"
TIMEZONE_KEY = "tokenCostUsageBucketTimeZone"


def micros(usd: float | None) -> int | None:
    return None if usd is None else round(usd * 1_000_000)


class CodexBarCostSource:
    id = "codexbar"

    def __init__(self, binary: str = "codexbar", providers: tuple[str, ...] = PROVIDERS) -> None:
        self.binary = binary
        self.providers = providers

    def collect(self, ctx: SourceContext) -> list[CaptureResult]:
        binary = shutil.which(self.binary)
        if binary is None:
            raise SourceError(f"{self.binary} not found on PATH")
        self._check_timezone(ctx)
        version = self._version(binary)
        results = []
        for provider in self.providers:
            proc = run_tool(
                [binary, "cost", "--provider", provider, "--days", str(HISTORY_DAYS), "--format", "json"],
                timeout=TIMEOUT_S,
            )
            if proc.returncode != 0:
                raise SourceError(f"codexbar cost --provider {provider} exited {proc.returncode}: {_tail(proc.stderr)}")
            result = parse_cost_export(proc.stdout, provider=provider, ctx=ctx, tool_version=version)
            if result is not None:
                results.append(result)
        return results

    def _version(self, binary: str) -> str:
        proc = run_tool([binary, "--version"], timeout=30)
        text = proc.stdout.decode().strip()
        if proc.returncode != 0 or not text:
            raise SourceError("could not read codexbar version")
        return text

    def _check_timezone(self, ctx: SourceContext) -> None:
        try:
            out = subprocess.run(
                ["defaults", "read", DEFAULTS_DOMAIN, TIMEZONE_KEY], capture_output=True, text=True, timeout=10
            )
        except FileNotFoundError:
            return  # not macOS; the CLI then buckets in the system timezone
        tz = out.stdout.strip()
        if out.returncode == 0 and tz and tz != ctx.settings.timezone:
            raise SourceError(
                f"CodexBar buckets days in {tz} but the ledger uses {ctx.settings.timezone}; "
                "change one of them so days line up"
            )


def parse_cost_export(raw: bytes, *, provider: str, ctx: SourceContext, tool_version: str) -> CaptureResult | None:
    """Turn one ``codexbar cost`` JSON export into a CaptureResult.

    Returns None when the provider has no local history at all. Raises
    SourceError when the export is partial or does not look like expected.
    """
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SourceError(f"codexbar {provider}: output is not JSON") from exc
    if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
        raise SourceError(f"codexbar {provider}: expected a one-element JSON list")
    report = payload[0]
    if "error" in report:
        raise SourceError(f"codexbar {provider}: {report['error'].get('message', report['error'])}")
    if report.get("provider") != provider:
        raise SourceError(f"codexbar {provider}: report is for {report.get('provider')!r}")
    daily = report.get("daily")
    if not isinstance(daily, list):
        raise SourceError(f"codexbar {provider}: missing daily list")
    if not report.get("historyCoverageIsEstablished", False):
        if daily:
            raise SourceError(f"codexbar {provider}: history scan is still incomplete; retry later")
        return None
    history_days = int(report.get("historyDays") or HISTORY_DAYS)
    if report.get("provenance") not in (None, "listPriceEstimate", "unknown"):
        raise SourceError(f"codexbar {provider}: unexpected cost provenance {report.get('provenance')!r}")

    rows: list[DayUsage] = []
    for entry in daily:
        rows.extend(_day_rows(entry, provider))

    today = local_today(ctx.settings, ctx.now_utc)
    return CaptureResult(
        series=SeriesKey("codexbar", provider, ctx.settings.machine.id, Scope.MACHINE),
        rows=rows,
        window_start=(today - timedelta(days=history_days - 1)).isoformat(),
        window_end=today.isoformat(),
        tool_version=tool_version,
        raw_name=f"codexbar-{provider}",
        raw=raw,
        meta={"history_days": history_days, "coverage": report.get("coverage")},
    )


def _int(entry: dict, key: str) -> int | None:
    value = entry.get(key)
    if value is None:
        return None
    if not isinstance(value, (int, float)) or value < 0:
        raise SourceError(f"field {key} has unexpected value {value!r}")
    return int(value)


def _day_rows(entry: dict, provider: str) -> list[DayUsage]:
    day = entry.get("date")
    if not isinstance(day, str) or len(day) != 10:
        raise SourceError(f"codexbar {provider}: bad date {day!r}")
    total = _int(entry, "totalTokens")
    if total is None:
        raise SourceError(f"codexbar {provider} {day}: missing totalTokens")
    inp = _int(entry, "inputTokens")
    cache_read = _int(entry, "cacheReadTokens")
    cache_write = _int(entry, "cacheCreationTokens")
    output = _int(entry, "outputTokens")
    reasoning = _int(entry, "reasoningTokens")

    if provider == "codex":
        # CodexBar's Codex input includes cached input; total = input + output.
        if inp is not None and output is not None and inp + output != total:
            raise SourceError(f"codexbar codex {day}: input+output != total")
        input_uncached = None if inp is None else inp - (cache_read or 0)
    else:
        # Claude-style: input excludes cache; total = input + cache read + cache write + output.
        parts = [inp, cache_read, cache_write, output]
        if all(p is not None for p in parts) and sum(parts) != total:
            raise SourceError(f"codexbar {provider} {day}: token parts do not add up to total")
        input_uncached = inp

    day_cost = entry.get("totalCost")
    breakdowns = entry.get("modelBreakdowns") or []
    rows = [
        DayUsage(
            day=day,
            model=DAY_TOTAL,
            total=total,
            input_uncached=input_uncached,
            cache_read=cache_read,
            cache_write=cache_write,
            output=output,
            reasoning=reasoning,
            cost_micros=micros(day_cost),
            # A day that mixes priced and unpriced models keeps the priced part as its
            # list-price figure; the model rows show which part is unpriced.
            cost_quality=CostQuality.LIST_PRICE if day_cost is not None else CostQuality.UNPRICED,
        )
    ]
    for b in breakdowns:
        model = b.get("modelName")
        tokens = _int(b, "totalTokens")
        if not isinstance(model, str) or not model or tokens is None:
            raise SourceError(f"codexbar {provider} {day}: malformed model breakdown {b!r}")
        cost = b.get("cost")
        rows.append(
            DayUsage(
                day=day,
                model=model,
                total=tokens,
                cost_micros=micros(cost),
                cost_quality=CostQuality.LIST_PRICE if cost is not None else CostQuality.UNPRICED,
            )
        )
    return rows


def _tail(stderr: bytes, limit: int = 400) -> str:
    return stderr.decode(errors="replace").strip()[-limit:]
