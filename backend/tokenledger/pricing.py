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
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

CODEXBAR_CATALOG = Path.home() / "Library/Caches/CodexBar/model-pricing/models-dev-v1.json"
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
