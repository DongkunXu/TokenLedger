"""List-price cost with CodexBar's own pricing rules.

Prices come from the models.dev catalog CodexBar caches (USD per million
tokens). Rules mirror CodexBar (``CostUsagePricing.swift``):

* Codex/OpenAI: a request whose input exceeds the long-context threshold is
  billed entirely at the ``context_over_200k`` rates. Cached input uses
  ``cache_read``. The threshold is 272K for the gpt-5 family and 200K for later
  models; this was read off CodexBar's own figures, which it reproduces exactly
  on every day from 2026-09-13 to 2026-10-03 (research/README.md).
* Claude: 5-minute cache writes use ``cache_write``; 1-hour cache writes cost
  twice the uncached input rate.

Validated against CodexBar's stored costs: Claude within 0.05%, Codex within
about 1% on days without Fast-mode surcharges (see research/README.md).

``claude_cost_usd`` is the full rule set CodexBar applies to Claude Code logs
(``CostUsagePricing.swift``, ``ModelsDevPricing.swift``), used by the
``claude-logs`` source on machines without CodexBar:

* the model id is normalized: ``anthropic.`` and Bedrock region prefixes and
  ``-vN:M`` suffixes are dropped, and a ``-YYYYMMDD`` date only when the base is
  in the built-in table. The normalized id is also the model name reported
* ``claude-opus-4-6`` and ``claude-sonnet-4-6`` always use built-in rates, with a
  long-context tier before 2026-03-13
* otherwise models.dev's ``anthropic`` provider (raw id, then normalized, each
  through CodexBar's list of id variants), then the built-in table, else no price
* a missing cache rate falls back to the input rate; ``context_over_200k`` rates
  apply to the whole request when input + cache reads + cache writes exceed the
  tier size (200K unless a ``tiers`` entry says otherwise)
* 1-hour cache writes cost twice the (tier) input rate
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone

PER_MILLION = 1_000_000


@dataclass(frozen=True)
class Catalog:
    providers: dict
    fetched_at: str | None = None

    @classmethod
    def from_models_dev(cls, raw: bytes) -> "Catalog":
        payload = json.loads(raw)
        return cls(providers=payload["catalog"]["providers"], fetched_at=payload.get("fetchedAt"))

    def rates(self, provider: str, model: str) -> dict | None:
        models = self.providers.get(provider, {}).get("models", {})
        entry = models.get(model)
        return entry.get("cost") if entry else None


def codex_long_context_threshold(model: str) -> int:
    return 272_000 if model.startswith("gpt-5") else 200_000


def codex_request_cost_usd(catalog: Catalog, model: str, input_tokens: int, cached: int, output: int) -> float | None:
    rates = catalog.rates("openai", model)
    if rates is None:
        return None
    if input_tokens > codex_long_context_threshold(model) and "context_over_200k" in rates:
        rates = {**rates, **rates["context_over_200k"]}
    uncached = max(0, input_tokens - cached)
    cache_rate = rates.get("cache_read", rates["input"])
    return (uncached * rates["input"] + cached * cache_rate + output * rates["output"]) / PER_MILLION


def claude_request_cost_usd(
    catalog: Catalog, model: str, *, input_tokens: int, cache_read: int, cache_write: int, output: int, one_hour_share: float
) -> float | None:
    rates = catalog.rates("anthropic", model)
    if rates is None:
        return None
    one_hour = cache_write * one_hour_share
    five_min = cache_write - one_hour
    return (
        input_tokens * rates["input"]
        + cache_read * rates["cache_read"]
        + five_min * rates["cache_write"]
        + one_hour * rates["input"] * 2
        + output * rates["output"]
    ) / PER_MILLION


def load_catalog() -> Catalog:
    """The catalog to price with (``catalog.py``): CodexBar's, else TokenLedger's own copy."""
    from .catalog import catalog_bytes

    return Catalog.from_models_dev(catalog_bytes())


# -- Claude, CodexBar's rules -------------------------------------------------------------

# (input, output, cache_write_5m, cache_read, threshold, input_above, output_above, cache_write_above,
# cache_read_above), USD per token. CostUsagePricing.swift.
Rates = tuple


def _flat(i: float, o: float, cw: float, cr: float) -> Rates:
    return (i, o, cw, cr, None, None, None, None, None)


def _long(i: float, o: float, cw: float, cr: float) -> Rates:
    return (i, o, cw, cr, 200_000, i * 2, o * 1.5, cw * 2, cr * 2)


CLAUDE_BUILTIN: dict[str, Rates] = {
    "claude-fable-5": _flat(1e-5, 5e-5, 1.25e-5, 1e-6),
    "claude-haiku-4-5-20251001": _flat(1e-6, 5e-6, 1.25e-6, 1e-7),
    "claude-haiku-4-5": _flat(1e-6, 5e-6, 1.25e-6, 1e-7),
    "claude-opus-4-5-20251101": _flat(5e-6, 2.5e-5, 6.25e-6, 5e-7),
    "claude-opus-4-5": _flat(5e-6, 2.5e-5, 6.25e-6, 5e-7),
    "claude-opus-4-6-20260205": _flat(5e-6, 2.5e-5, 6.25e-6, 5e-7),
    "claude-opus-4-6": _flat(5e-6, 2.5e-5, 6.25e-6, 5e-7),
    "claude-opus-4-7": _flat(5e-6, 2.5e-5, 6.25e-6, 5e-7),
    "claude-opus-4-8": _flat(5e-6, 2.5e-5, 6.25e-6, 5e-7),
    "claude-sonnet-4-5": _long(3e-6, 1.5e-5, 3.75e-6, 3e-7),
    "claude-sonnet-4-6": _flat(3e-6, 1.5e-5, 3.75e-6, 3e-7),
    "claude-sonnet-4-5-20250929": _long(3e-6, 1.5e-5, 3.75e-6, 3e-7),
    "claude-opus-4-20250514": _flat(1.5e-5, 7.5e-5, 1.875e-5, 1.5e-6),
    "claude-opus-4-1": _flat(1.5e-5, 7.5e-5, 1.875e-5, 1.5e-6),
    "claude-sonnet-4-20250514": _long(3e-6, 1.5e-5, 3.75e-6, 3e-7),
}
CLAUDE_HISTORICAL_UNTIL = datetime.fromtimestamp(1_773_360_000, timezone.utc)  # 2026-03-13T00:00:00Z
CLAUDE_HISTORICAL: dict[str, Rates] = {
    "claude-opus-4-6": (5e-6, 2.5e-5, 6.25e-6, 5e-7, 200_000, 1e-5, 3.75e-5, 1.25e-5, 1e-6),
    "claude-sonnet-4-6": _long(3e-6, 1.5e-5, 3.75e-6, 3e-7),
}


def normalize_claude_model(raw: str) -> str:
    t = raw.strip()
    if t.startswith("anthropic."):
        t = t[len("anthropic.") :]
    if "." in t and "claude-" in t:
        tail = t[t.rindex(".") + 1 :]
        if tail.startswith("claude-"):
            t = tail
    t = re.sub(r"-v\d+:\d+$", "", t)
    m = re.search(r"-\d{8}$", t)
    if m and t[: m.start()] in CLAUDE_BUILTIN:
        return t[: m.start()]
    return t


def _id_candidates(raw: str) -> list[str]:
    """ModelsDevModelIDNormalizer.candidates: the id and the variants models.dev may list it under."""
    out: list[str] = []

    def add(v: str) -> None:
        v = v.strip()
        if v and v not in out:
            out.append(v)

    t = raw.strip()
    add(t)
    if t.startswith("openai/"):
        add(t[len("openai/") :])
    if t.startswith("anthropic."):
        add(t[len("anthropic.") :])
    if "." in t and "claude-" in t:
        tail = t[t.rindex(".") + 1 :]
        if tail.startswith("claude-"):
            add(tail)
    i = 0
    while i < len(out):
        c = out[i]
        if "@" in c:
            base, suffix = c.split("@", 1)
            if re.fullmatch(r"\d{8}", suffix):
                add(f"{base}-{suffix}")
            add(base)
        elif c.startswith("claude-"):
            add(c + "@default")
        for pattern in (r"-\d{4}-\d{2}-\d{2}$", r"-\d{8}$"):
            m = re.search(pattern, c)
            if m:
                add(c[: m.start()])
        m = re.search(r"-v\d+:\d+$", c)
        if m:
            add(c[: m.start()] + c[m.end() :])
        i += 1
    return out


def _models_dev_rates(model: dict) -> Rates | None:
    cost = model.get("cost") or {}
    if not isinstance(cost.get("input"), (int, float)) or not isinstance(cost.get("output"), (int, float)):
        return None

    def per(v):
        return None if v is None else v / PER_MILLION

    above = cost.get("context_over_200k")
    threshold = None
    if above is not None:
        threshold = 200_000
        for tier in cost.get("tiers") or []:
            meta = tier.get("tier") or {}
            if meta.get("type") == "context" and (meta.get("size") or 0) > 0 and {k: v for k, v in tier.items() if k != "tier"} == above:
                threshold = meta["size"]
                break
    above = above or {}
    inp = per(cost["input"])
    return (
        inp,
        per(cost["output"]),
        per(cost.get("cache_write")) if cost.get("cache_write") is not None else inp,
        per(cost.get("cache_read")) if cost.get("cache_read") is not None else inp,
        threshold,
        per(above.get("input")),
        per(above.get("output")),
        per(above.get("cache_write")),
        per(above.get("cache_read")),
    )


def _models_dev_lookup(catalog: Catalog, model: str) -> Rates | None:
    provider = catalog.providers.get("anthropic")
    if not provider:
        return None
    models = provider.get("models") or {}
    by_id: dict[str, dict] | None = None
    normalized = normalize_claude_model(model)
    for wanted in [model] if normalized == model else [model, normalized]:
        for candidate in _id_candidates(wanted):
            entry = models.get(candidate)
            if entry is not None and _models_dev_rates(entry) is not None:
                return _models_dev_rates(entry)
            if by_id is None:
                by_id = {}
                for m in models.values():
                    if isinstance(m.get("id"), str) and _models_dev_rates(m) is not None:
                        by_id.setdefault(m["id"].strip(), m)
            if candidate in by_id:
                return _models_dev_rates(by_id[candidate])
    return None


def claude_rates(catalog: Catalog, model: str, at: datetime | None) -> Rates | None:
    key = normalize_claude_model(model)
    if at is not None and key in CLAUDE_HISTORICAL and key in CLAUDE_BUILTIN:
        return CLAUDE_HISTORICAL[key] if at < CLAUDE_HISTORICAL_UNTIL else CLAUDE_BUILTIN[key]
    if key.lower().startswith("claude-"):
        rates = _models_dev_lookup(catalog, model)
        if rates is not None:
            return rates
    return CLAUDE_BUILTIN.get(key)


def claude_cost_usd(
    catalog: Catalog,
    model: str,
    *,
    input_tokens: int,
    cache_read: int,
    cache_write: int,
    cache_write_1h: int,
    output: int,
    at: datetime | None,
) -> float | None:
    """One Claude request at list price with CodexBar's rules; None when the model has no price."""
    rates = claude_rates(catalog, model, at)
    if rates is None:
        return None
    i_rate, o_rate, cw_rate, cr_rate, threshold, i2, o2, cw2, cr2 = rates
    one_hour = min(max(0, cache_write_1h), cache_write)
    five_min = cache_write - one_hour
    if threshold is not None and (input_tokens + cache_read + cache_write) > threshold:
        i_rate = i2 if i2 is not None else i_rate
        cr_rate = cr2 if cr2 is not None else cr_rate
        cw_rate = cw2 if cw2 is not None else cw_rate
        o_rate = o2 if o2 is not None else o_rate
    return input_tokens * i_rate + cache_read * cr_rate + five_min * cw_rate + one_hour * i_rate * 2 + output * o_rate
