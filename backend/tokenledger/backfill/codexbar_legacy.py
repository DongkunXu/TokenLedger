"""Backfill from CodexBar's old cost caches (``claude-v*.json``, ``codex-v*.json``).

Older CodexBar versions left their caches behind. They hold per-request rows
from log files that no longer exist, which makes them the only record of that
usage. What was learned about them (research/README.md):

* ``claude-v2`` day totals double count copied messages; its rows are fine once
  deduplicated by ``messageId + requestId``. v4/v5 rows carry CodexBar's own cost.
* ``codex-v9`` counted history copied into forked sessions; ``codex-v10`` day
  totals exclude it. The newest cache that has a day's total wins.

Claude: the value for a day is CodexBar's current figure (from the latest
archived capture) plus the requests that only survive in old caches.
Codex: days CodexBar still reports are left alone; other days come from the
newest old cache that covers them.
"""

from __future__ import annotations

import gzip
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..models import DAY_TOTAL, CostQuality, DayUsage
from ..pricing import Catalog, claude_request_cost_usd, codex_request_cost_usd

CLAUDE_CACHES = ("claude-v5", "claude-v4", "claude-v2")  # newest first
CLAUDE_CURRENT = "claude-v6"
CODEX_CACHES = ("codex-v10", "codex-v9", "codex-v6")  # newest first


def load_json(folder: Path, stem: str) -> dict:
    gz = folder / f"{stem}.json.gz"
    if gz.exists():
        with gzip.open(gz, "rb") as fh:
            return json.loads(fh.read())
    return json.loads((folder / f"{stem}.json").read_text())


def load_catalog(folder: Path) -> Catalog:
    gz = folder / "models-dev-v1.json.gz"
    raw = gzip.decompress(gz.read_bytes()) if gz.exists() else (folder / "models-dev-v1.json").read_bytes()
    return Catalog.from_models_dev(raw)


@dataclass
class _Acc:
    total: int = 0
    input_uncached: int = 0
    cache_read: int = 0
    cache_write: int = 0
    output: int = 0
    cost_micros: int = 0
    recomputed: bool = False
    unpriced: bool = False

    def add_usage(self, u: DayUsage) -> None:
        self.total += u.total
        self.input_uncached += u.input_uncached or 0
        self.cache_read += u.cache_read or 0
        self.cache_write += u.cache_write or 0
        self.output += u.output or 0
        if u.cost_micros is None:
            self.unpriced = True
        else:
            self.cost_micros += u.cost_micros
        self.recomputed |= u.cost_quality == CostQuality.RECOMPUTED


@dataclass
class ClaudeBackfill:
    rows: list[DayUsage]
    added_requests: int
    added_by_cache: dict[str, int] = field(default_factory=dict)
    one_hour_share: float = 0.0


def claude_backfill(folder: Path, base_rows: list[DayUsage], catalog: Catalog) -> ClaudeBackfill:
    """Current CodexBar day/model values plus requests that only exist in old caches."""
    current_keys = {_claude_key(r) for r in _claude_rows(load_json(folder, CLAUDE_CURRENT))}
    caches = {stem: _claude_rows(load_json(folder, stem)) for stem in CLAUDE_CACHES}
    # v2 rows have no 1-hour/5-minute cache-write split. Take the share from the requests v2 and
    # v4 have in common: priced with it, those rows reproduce CodexBar's stored v4 costs (0.05%).
    v2_keys = {_claude_key(r) for r in caches["claude-v2"]}
    shared = {_claude_key(r): r for r in caches["claude-v4"] if _claude_key(r) in v2_keys}
    share = _one_hour_share(list(shared.values()))

    extra: dict[tuple[str, str], dict] = {}
    by_cache: dict[str, int] = defaultdict(int)
    for stem in CLAUDE_CACHES:  # newest first, so the newest copy of a request wins
        for r in caches[stem]:
            key = _claude_key(r)
            if key in current_keys or key in extra:
                continue
            extra[key] = r
            by_cache[stem] += 1

    additions: dict[tuple[str, str], _Acc] = defaultdict(_Acc)
    for r in extra.values():
        u = _claude_row_usage(r, catalog, share)
        additions[(u.day, u.model)].add_usage(u)
        additions[(u.day, DAY_TOTAL)].add_usage(u)

    merged: dict[tuple[str, str], _Acc] = defaultdict(_Acc)
    for b in base_rows:
        merged[(b.day, b.model)].add_usage(b)
    for key, acc in additions.items():
        m = merged[key]
        m.total += acc.total
        m.input_uncached += acc.input_uncached
        m.cache_read += acc.cache_read
        m.cache_write += acc.cache_write
        m.output += acc.output
        m.cost_micros += acc.cost_micros
        m.recomputed |= acc.recomputed
        m.unpriced |= acc.unpriced

    touched_days = {day for day, _ in additions}
    rows = [
        _acc_to_usage(day, model, acc, with_parts=model == DAY_TOTAL)
        for (day, model), acc in sorted(merged.items())
        if day in touched_days
    ]
    return ClaudeBackfill(rows=rows, added_requests=len(extra), added_by_cache=dict(by_cache), one_hour_share=share)


def codex_backfill(folder: Path, reported_days: set[str], catalog: Catalog) -> list[DayUsage]:
    """Codex days that CodexBar no longer reports, from the newest old cache covering each day."""
    caches = {stem: load_json(folder, stem) for stem in CODEX_CACHES}
    chosen: dict[str, tuple[str, dict[str, list[int]]]] = {}
    for stem in CODEX_CACHES:  # day totals first, newest cache first
        for day, models in caches[stem].get("days", {}).items():
            if day not in reported_days and day not in chosen:
                chosen[day] = (stem, models)
    for stem in CODEX_CACHES:  # then days that only survive as rows
        for day, models in _codex_rows_by_day(caches[stem]).items():
            if day not in reported_days and day not in chosen:
                chosen[day] = (stem, models)

    rows: list[DayUsage] = []
    for day in sorted(chosen):
        stem, models = chosen[day]
        rates = _codex_cost_per_token(caches[stem], day, catalog)
        day_acc = _Acc()
        for model, packed in sorted(models.items()):
            inp, cached, out = packed[0], packed[1], packed[2]
            rate = rates.get(model)
            cost = None if rate is None else round((inp + out) * rate * 1_000_000)
            u = DayUsage(
                day=day,
                model=model,
                total=inp + out,
                cost_micros=cost,
                cost_quality=CostQuality.RECOMPUTED if cost is not None else CostQuality.UNPRICED,
            )
            rows.append(u)
            day_acc.add_usage(
                DayUsage(day=day, model=model, total=inp + out, input_uncached=inp - cached, cache_read=cached,
                         output=out, cost_micros=cost, cost_quality=u.cost_quality)
            )
        rows.append(_acc_to_usage(day, DAY_TOTAL, day_acc, with_parts=True, codex=True))
    return rows


# --- Claude helpers ---------------------------------------------------------------------------


def _claude_rows(cache: dict) -> list[dict]:
    return [r for entry in cache.get("files", {}).values() for r in entry.get("claudeRows", [])]


def _claude_key(r: dict) -> tuple[str, str]:
    mid, rid = r.get("messageId"), r.get("requestId")
    if not mid or not rid:
        raise ValueError(f"Claude cache row without messageId/requestId: {r}")
    return (mid, rid)


def _one_hour_share(rows: list[dict]) -> float:
    total = sum(r["cacheCreate"] for r in rows)
    if total == 0:
        raise ValueError("cannot derive the 1-hour cache-write share: no cache writes in the requests shared by claude-v2 and claude-v4")
    return sum(r.get("cacheCreate1h", 0) for r in rows) / total


def _claude_row_usage(r: dict, catalog: Catalog, share: float) -> DayUsage:
    total = r["input"] + r["cacheRead"] + r["cacheCreate"] + r["output"]
    if r.get("costPriced") and r.get("costNanos"):
        cost, quality = round(r["costNanos"] / 1000), CostQuality.LIST_PRICE
    else:
        usd = claude_request_cost_usd(
            catalog, r["model"], input_tokens=r["input"], cache_read=r["cacheRead"],
            cache_write=r["cacheCreate"], output=r["output"], one_hour_share=share,
        )
        cost = None if usd is None else round(usd * 1_000_000)
        quality = CostQuality.RECOMPUTED if cost is not None else CostQuality.UNPRICED
    return DayUsage(
        day=r["dayKey"], model=r["model"], total=total, input_uncached=r["input"], cache_read=r["cacheRead"],
        cache_write=r["cacheCreate"], output=r["output"], cost_micros=cost, cost_quality=quality,
    )


# --- Codex helpers ----------------------------------------------------------------------------


def _codex_rows(cache: dict):
    seen = set()
    for entry in cache.get("files", {}).values():
        for r in entry.get("codexRows", []):
            key = (r.get("turnID"), r.get("eventIndex"), r["input"], r["cached"], r["output"], r["day"])
            if key in seen:
                continue
            seen.add(key)
            yield r


def _codex_rows_by_day(cache: dict) -> dict[str, dict[str, list[int]]]:
    out: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0, 0]))
    for r in _codex_rows(cache):
        packed = out[r["day"]][r["model"]]
        packed[0] += r["input"]
        packed[1] += r["cached"]
        packed[2] += r["output"]
    return out


def _codex_cost_per_token(cache: dict, day: str, catalog: Catalog) -> dict[str, float]:
    """USD per token for each model on a day, priced request by request from the cache's rows."""
    cost: dict[str, float] = defaultdict(float)
    tokens: dict[str, int] = defaultdict(int)
    unpriced: set[str] = set()
    for r in _codex_rows(cache):
        if r["day"] != day:
            continue
        # Old caches keep only the day; its middle is close enough for date-based price changes.
        at = datetime.fromisoformat(f"{day}T12:00:00+00:00")
        usd = codex_request_cost_usd(catalog, r["model"], r["input"], r["cached"], r["output"], at=at)
        if usd is None:
            unpriced.add(r["model"])
            continue
        cost[r["model"]] += usd
        tokens[r["model"]] += r["input"] + r["output"]
    return {m: cost[m] / tokens[m] for m in tokens if tokens[m] and m not in unpriced}


# --- shared -----------------------------------------------------------------------------------


def _acc_to_usage(day: str, model: str, acc: _Acc, *, with_parts: bool, codex: bool = False) -> DayUsage:
    if acc.unpriced and acc.cost_micros == 0:
        cost, quality = None, CostQuality.UNPRICED
    else:
        cost = acc.cost_micros
        quality = CostQuality.RECOMPUTED if acc.recomputed else CostQuality.LIST_PRICE
    return DayUsage(
        day=day,
        model=model,
        total=acc.total,
        input_uncached=acc.input_uncached if with_parts else None,
        cache_read=acc.cache_read if with_parts else None,
        cache_write=(None if codex else acc.cache_write) if with_parts else None,
        output=acc.output if with_parts else None,
        cost_micros=cost,
        cost_quality=quality,
    )
