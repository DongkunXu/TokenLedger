"""List-price cost with CodexBar's own pricing rules.

Prices come from the models.dev catalog CodexBar caches (USD per million
tokens). Rules mirror CodexBar (``CostUsagePricing.swift``):

* Codex/OpenAI: ``codex_request_cost_usd`` is CodexBar v0.78.0's rule set
  (``codex_rates``). A request whose input (cached input included) exceeds the
  model's threshold is billed entirely at the long-context rates. The threshold
  is a matching ``tiers`` entry's size, else 272K for the models OpenAI prices
  that way (``CODEX_BUILTIN``: gpt-5.4/5.5, the gpt-5.6 and gpt-6 families),
  else 200K.
* Claude: 5-minute cache writes use ``cache_write``; 1-hour cache writes cost
  twice the uncached input rate.

Validated against CodexBar's stored costs: Claude within 0.05%, Codex within
about 1% on days without Fast-mode surcharges (see research/README.md).

``claude_cost_usd`` is the full rule set CodexBar v0.78.0 applies to Claude Code
logs (``CostUsagePricing.swift``, ``ModelsDevPricing.swift``,
``ModelsDevPricingTargetResolver.swift``), used by the ``claude-logs`` source on
machines without CodexBar:

* the model id is normalized: ``anthropic.`` and Bedrock region prefixes and
  ``-vN:M`` suffixes are dropped, and a ``-YYYYMMDD`` date only when the base is
  in the built-in table. The normalized id is also the model name reported
* ``claude-opus-4-6`` and ``claude-sonnet-4-6`` always use built-in rates, with a
  long-context tier before 2026-03-13
* otherwise models.dev, then the built-in table, else no price. Which models.dev
  provider is asked follows the id (``claude_models_dev_targets``): ``route/model``
  ids ask that route only (OpenAI-compatible routes such as ``openai``, ``deepseek``,
  ``kimi-coding`` and ``opencode-go`` first, then ``anthropic``, ``google``,
  ``moonshot``, ``kimi-for-coding`` and ``minimax``); bare ids of a known family ask
  its vendor (``claude-*`` Anthropic, ``gpt-*``/``o3`` OpenAI, ``gemini-*`` Google,
  ``kimi-*`` Moonshot, ``k3`` Kimi for Coding, ``minimax-*``, ``deepseek-*``); any
  other bare id is priced only when exactly one of those vendors lists it.
  ``k3[1m]`` falls back to Kimi for Coding's ``k3``. Each id is tried raw, then
  normalized, each through CodexBar's list of id variants
* a missing cache rate falls back to the input rate; ``context_over_200k`` rates
  apply to the whole request when input + cache reads + cache writes exceed the
  tier size (200K unless a ``tiers`` entry says otherwise; 272K for the OpenAI
  models CodexBar bundles with that boundary)
* 1-hour cache writes cost twice the (tier) input rate
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from .catalog import catalog_bytes, id_candidates

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

    def price(self, provider: str, model: str) -> "ModelsDevPrice | None":
        """One model's rates from one provider (``ModelsDevCatalog.pricing(providerID:modelID:)``):
        the first id variant listed under its key, else under a priced model's ``id``."""
        key = provider.strip().lower()
        entry = self.providers.get(key)
        if not entry:
            return None
        provider_id = (entry["id"] if isinstance(entry.get("id"), str) else key).strip().lower()
        models = entry.get("models") or {}
        by_id: dict[str, dict] | None = None
        for candidate in id_candidates(model):
            found = _models_dev_price(provider_id, models.get(candidate))
            if found is not None:
                return found
            if by_id is None:
                by_id = {}
                for m in models.values():
                    if _priceable(m):
                        by_id.setdefault(m["id"].strip(), m)
            found = _models_dev_price(provider_id, by_id.get(candidate))
            if found is not None:
                return found
        return None


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
    return Catalog.from_models_dev(catalog_bytes())


# -- Claude, CodexBar's rules -------------------------------------------------------------


@dataclass(frozen=True)
class ClaudeRates:
    """USD per token (``CostUsagePricing.ClaudePricing``). Above ``threshold`` prompt tokens the
    ``*_above`` rates apply to the whole request; a missing one keeps the normal rate."""

    input: float
    output: float
    cache_write: float  # 5-minute writes
    cache_read: float
    threshold: int | None = None
    input_above: float | None = None
    output_above: float | None = None
    cache_write_above: float | None = None
    cache_read_above: float | None = None


@dataclass(frozen=True)
class ModelsDevPrice:
    """One models.dev model's rates, USD per token (``ModelsDevPricingInfo``)."""

    provider: str  # normalized provider id
    model: str  # the catalog model's id
    input: float
    output: float
    cache_read: float | None
    cache_write: float | None
    threshold: int | None
    input_above: float | None
    output_above: float | None
    cache_read_above: float | None
    cache_write_above: float | None


_SONNET_LONG = ClaudeRates(3e-6, 1.5e-5, 3.75e-6, 3e-7, 200_000, 6e-6, 2.25e-5, 7.5e-6, 6e-7)
CLAUDE_BUILTIN: dict[str, ClaudeRates] = {
    "claude-fable-5": ClaudeRates(1e-5, 5e-5, 1.25e-5, 1e-6),
    "claude-haiku-4-5-20251001": ClaudeRates(1e-6, 5e-6, 1.25e-6, 1e-7),
    "claude-haiku-4-5": ClaudeRates(1e-6, 5e-6, 1.25e-6, 1e-7),
    "claude-opus-4-5-20251101": ClaudeRates(5e-6, 2.5e-5, 6.25e-6, 5e-7),
    "claude-opus-4-5": ClaudeRates(5e-6, 2.5e-5, 6.25e-6, 5e-7),
    "claude-opus-4-6-20260205": ClaudeRates(5e-6, 2.5e-5, 6.25e-6, 5e-7),
    "claude-opus-4-6": ClaudeRates(5e-6, 2.5e-5, 6.25e-6, 5e-7),
    "claude-opus-4-7": ClaudeRates(5e-6, 2.5e-5, 6.25e-6, 5e-7),
    "claude-opus-4-8": ClaudeRates(5e-6, 2.5e-5, 6.25e-6, 5e-7),
    "claude-sonnet-4-5": _SONNET_LONG,
    "claude-sonnet-4-6": ClaudeRates(3e-6, 1.5e-5, 3.75e-6, 3e-7),
    "claude-sonnet-4-5-20250929": _SONNET_LONG,
    "claude-opus-4-20250514": ClaudeRates(1.5e-5, 7.5e-5, 1.875e-5, 1.5e-6),
    "claude-opus-4-1": ClaudeRates(1.5e-5, 7.5e-5, 1.875e-5, 1.5e-6),
    "claude-sonnet-4-20250514": _SONNET_LONG,
}
CLAUDE_HISTORICAL_UNTIL = datetime.fromtimestamp(1_773_360_000, timezone.utc)  # 2026-03-13T00:00:00Z
CLAUDE_HISTORICAL: dict[str, ClaudeRates] = {
    "claude-opus-4-6": ClaudeRates(5e-6, 2.5e-5, 6.25e-6, 5e-7, 200_000, 1e-5, 3.75e-5, 1.25e-5, 1e-6),
    "claude-sonnet-4-6": _SONNET_LONG,
}

@dataclass(frozen=True)
class CodexRates:
    """USD per token (``CostUsagePricing.CodexPricing``). Above ``threshold`` input tokens the
    ``*_above`` rates apply to the whole request; a missing one keeps the normal rate. Codex logs
    record no cache writes, so CodexBar's cache-write rates never apply and are left out."""

    input: float
    output: float
    cache_read: float | None
    threshold: int | None = None
    input_above: float | None = None
    output_above: float | None = None
    cache_read_above: float | None = None


def _long_context(input: float, cached: float | None, output: float, output_long: float) -> CodexRates:
    """OpenAI's long-context pricing: above 272K input tokens, twice the input rates and the long output rate."""
    return CodexRates(input, output, cached, 272_000, input * 2, output_long, None if cached is None else cached * 2)


# CodexBar's bundled OpenAI table (``CostUsagePricing.codex``): the fallback when models.dev has
# no entry, the cache-read and long-context rates an entry lacks, and the models that switch to
# long-context rates above 272K input tokens (Claude and Codex paths alike).
CODEX_BUILTIN: dict[str, CodexRates] = {
    "gpt-5": CodexRates(1.25e-6, 1e-5, 1.25e-7),
    "gpt-5-codex": CodexRates(1.25e-6, 1e-5, 1.25e-7),
    "gpt-5-mini": CodexRates(2.5e-7, 2e-6, 2.5e-8),
    "gpt-5-nano": CodexRates(5e-8, 4e-7, 5e-9),
    "gpt-5-pro": CodexRates(1.5e-5, 1.2e-4, None),
    "gpt-5.1": CodexRates(1.25e-6, 1e-5, 1.25e-7),
    "gpt-5.1-codex": CodexRates(1.25e-6, 1e-5, 1.25e-7),
    "gpt-5.1-codex-max": CodexRates(1.25e-6, 1e-5, 1.25e-7),
    "gpt-5.1-codex-mini": CodexRates(2.5e-7, 2e-6, 2.5e-8),
    "gpt-5.2": CodexRates(1.75e-6, 1.4e-5, 1.75e-7),
    "gpt-5.2-codex": CodexRates(1.75e-6, 1.4e-5, 1.75e-7),
    "gpt-5.2-pro": CodexRates(2.1e-5, 1.68e-4, None),
    "gpt-5.3-codex": CodexRates(1.75e-6, 1.4e-5, 1.75e-7),
    "gpt-5.3-codex-spark": CodexRates(0, 0, 0),  # research preview
    "gpt-5.4": _long_context(2.5e-6, 2.5e-7, 1.5e-5, 2.25e-5),
    "gpt-5.4-mini": CodexRates(7.5e-7, 4.5e-6, 7.5e-8),
    "gpt-5.4-nano": CodexRates(2e-7, 1.25e-6, 2e-8),
    "gpt-5.4-pro": _long_context(3e-5, None, 1.8e-4, 2.7e-4),
    "gpt-5.5": _long_context(5e-6, 5e-7, 3e-5, 4.5e-5),
    "gpt-5.5-pro": _long_context(3e-5, None, 1.8e-4, 2.7e-4),
    "gpt-6-astra": _long_context(1e-5, 1e-6, 5e-5, 7.5e-5),
    "gpt-6.1-sol": _long_context(2e-6, 1e-7, 1e-5, 1.5e-5),
    "gpt-6-sol": _long_context(2e-6, 2e-7, 1e-5, 1.5e-5),
    "gpt-6-luna": _long_context(1e-7, 1e-8, 5e-7, 7.5e-7),
    "gpt-5.6-sol": _long_context(4e-6, 4e-7, 2e-5, 3e-5),  # repriced from $5/$30 on 2026-08-21
    "gpt-5.6-terra": _long_context(2e-6, 2e-7, 1.2e-5, 1.8e-5),
    "gpt-5.6-luna": _long_context(2e-7, 2e-8, 1.2e-6, 1.8e-6),
    "gpt-5.6-cyber": CodexRates(1.25e-5, 7.5e-5, 1.25e-6),  # no long-context tier published
    "gpt-5.5-cyber": CodexRates(1.25e-5, 7.5e-5, 1.25e-6),
}
CODEX_BUILTIN_MODELS = frozenset(CODEX_BUILTIN)
CODEX_272K_MODELS = frozenset(m for m, r in CODEX_BUILTIN.items() if r.threshold == 272_000)
# Rates before a repricing (``codexHistoricalPricing``); they win over the catalog for older requests.
CODEX_HISTORICAL: dict[str, tuple[datetime, CodexRates]] = {
    "gpt-5.6-sol": (datetime(2026, 8, 21, tzinfo=timezone.utc), _long_context(5e-6, 5e-7, 3e-5, 4.5e-5)),
    "gpt-5.6-terra": (datetime(2026, 7, 30, tzinfo=timezone.utc), _long_context(2.5e-6, 2.5e-7, 1.5e-5, 2.25e-5)),
    "gpt-5.6-luna": (datetime(2026, 7, 30, tzinfo=timezone.utc), _long_context(1e-6, 1e-7, 6e-6, 9e-6)),
}
_CODEX_ALIASES = {
    "gpt-5.6": "gpt-5.6-sol",
    "gpt-reserve": "gpt-5.6-luna",
    "gpt-daybreak-blue-latest": "gpt-5.6-sol",
    "gpt-daybreak-red-latest": "gpt-5.6-cyber",
}
# Routes written by Codex-compatible clients (``codexModelsDevProviderIDs``), and the provider a
# route also falls back to (``ModelsDevPricingTargetResolver``).
CODEX_ROUTES = frozenset({"deepseek", "kimi-coding", "kimi-for-coding", "openai", "opencode", "opencode-free", "opencode-go"})
_ROUTE_FALLBACK = {"kimi-coding": "kimi-for-coding", "opencode-free": "opencode"}
# First-party vendors a Claude Code model id may be priced from (``claudeFirstPartyModelsDevProviderIDs``).
CLAUDE_FIRST_PARTY = ("anthropic", "openai", "google", "moonshot", "kimi-for-coding", "minimax", "deepseek")


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


def normalize_codex_model(raw: str) -> str:
    """``CostUsagePricing.normalizeCodexModel``."""
    t = raw.strip()
    if t.startswith("openai/"):
        t = t[len("openai/") :]
    if t in _CODEX_ALIASES:
        return _CODEX_ALIASES[t]
    if t in CODEX_BUILTIN_MODELS:
        return t
    m = re.search(r"-\d{4}-\d{2}-\d{2}$", t)
    if m and t[: m.start()] in CODEX_BUILTIN_MODELS:
        return t[: m.start()]
    return t


def _priceable(model) -> bool:
    """A model CodexBar can read (a string id) with input and output rates."""
    if not isinstance(model, dict) or not isinstance(model.get("id"), str):
        return False
    cost = model.get("cost")
    return isinstance(cost, dict) and _number(cost.get("input")) and _number(cost.get("output"))


def _number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _models_dev_price(provider: str, model: dict | None) -> ModelsDevPrice | None:
    """``ModelsDevModel.pricing``: USD per token, and the size above which ``context_over_200k``
    applies (a matching ``tiers`` entry, else 272K for bundled OpenAI models, else 200K)."""
    if model is None or not _priceable(model):
        return None
    cost = model["cost"]
    above = cost.get("context_over_200k")
    threshold = None
    if above is not None:
        for tier in cost.get("tiers") or []:
            meta = tier.get("tier") or {}
            if meta.get("type") == "context" and (meta.get("size") or 0) > 0 and _same_rates({**tier, "tier": None}, above):
                threshold = meta["size"]
                break
        else:
            threshold = 272_000 if provider == "openai" and normalize_codex_model(model["id"]) in CODEX_272K_MODELS else 200_000
    above = above or {}

    def per(v):
        return None if v is None else v / PER_MILLION

    return ModelsDevPrice(
        provider=provider,
        model=model["id"],
        input=per(cost["input"]),
        output=per(cost["output"]),
        cache_read=per(cost.get("cache_read")),
        cache_write=per(cost.get("cache_write")),
        threshold=threshold,
        input_above=per(above.get("input")),
        output_above=per(above.get("output")),
        cache_read_above=per(above.get("cache_read")),
        cache_write_above=per(above.get("cache_write")),
    )


def _same_rates(a: dict, b: dict) -> bool:
    return all(a.get(k) == b.get(k) for k in ("input", "output", "cache_read", "cache_write", "tier"))


def _route_targets(model: str) -> list[tuple[str, str]]:
    """``codexModelsDevPricingTargets`` for a ``route/model`` id: the route (and its fallback)
    with the route prefix dropped; ``openai`` also asks for its normalized id."""
    route, _, rest = model.partition("/")
    route = route.lower()
    if route not in CODEX_ROUTES or not rest or rest.startswith("/") or rest.endswith("/"):
        return []
    targets = [(route, rest)] + ([(_ROUTE_FALLBACK[route], rest)] if route in _ROUTE_FALLBACK else [])
    if route == "openai" and normalize_codex_model(rest) != rest:
        targets.append(("openai", normalize_codex_model(rest)))
    return targets


def _preferred_vendors(model: str) -> tuple[str, ...] | None:
    """The vendor that owns a recognizable bare model family (``claudeFirstPartyModelsDevPreferredProviderIDs``)."""
    m = normalize_claude_model(model).lower()
    if m.startswith("claude-"):
        return ("anthropic",)
    if any(m == f or m.startswith(f + "-") for f in ("o1", "o3", "o4")) or m.startswith(("gpt-", "chatgpt-", "text-embedding-")):
        return ("openai",)
    if m.startswith(("gemini-", "gemma-", "deep-research-", "veo-", "lyria-")):
        return ("google",)
    if m in ("kimi-for-coding", "k3", "k3[1m]") or m.startswith("k3-"):
        return ("kimi-for-coding",)
    if m.startswith(("kimi-", "moonshot-")):
        return ("moonshot", "kimi-for-coding")
    if m.startswith("minimax-"):
        return ("minimax",)
    if m.startswith("deepseek-"):
        return ("deepseek",)
    return None


def _claude_model_ids(model: str) -> list[str]:
    normalized = normalize_claude_model(model)
    return [model] if normalized == model else [model, normalized]


def claude_models_dev_targets(model: str) -> list[tuple[str, str]]:
    """The (provider, model) pairs models.dev is asked for, in order (``claudeModelsDevPricingTargets``)."""
    t = model.strip()
    if not t:
        return []
    if "/" in t:
        targets = _route_targets(t)
        if not targets:
            route, _, rest = t.partition("/")
            route = route.lower()
            if route not in CLAUDE_FIRST_PARTY or not rest:
                return []
            targets = [(route, m) for m in _claude_model_ids(rest)]
    else:
        ids = _claude_model_ids(t)
        targets = [(p, m) for p in _preferred_vendors(t) or CLAUDE_FIRST_PARTY for m in ids]
    # Claude Code's documented context-window alias stays inside Kimi for Coding, after every exact match.
    if any(p == "kimi-for-coding" and m.strip() == "k3[1m]" for p, m in targets):
        targets.append(("kimi-for-coding", "k3"))
    return targets


def claude_models_dev_price(catalog: Catalog, model: str) -> ModelsDevPrice | None:
    """``claudeModelsDevLookup``: the first listed target for a routed or recognizable id; any other
    id only when every match comes from one provider."""
    t = model.strip()
    decisive = "/" in t or _preferred_vendors(t) is not None
    matches: list[ModelsDevPrice] = []
    for provider, model_id in claude_models_dev_targets(model):
        found = catalog.price(provider, model_id)
        if found is not None:
            if decisive:
                return found
            matches.append(found)
    return matches[0] if len({m.provider for m in matches}) == 1 else None


def _claude_rates_from_models_dev(price: ModelsDevPrice) -> ClaudeRates:
    # OpenAI's bundled 272K boundary also applies to OpenAI models used through Claude Code.
    bundled = 272_000 if price.provider == "openai" and normalize_codex_model(price.model) in CODEX_272K_MODELS else None
    return ClaudeRates(
        input=price.input,
        output=price.output,
        cache_write=price.cache_write if price.cache_write is not None else price.input,
        cache_read=price.cache_read if price.cache_read is not None else price.input,
        threshold=bundled if bundled is not None else price.threshold,
        input_above=price.input_above,
        output_above=price.output_above,
        cache_write_above=price.cache_write_above,
        cache_read_above=price.cache_read_above,
    )


def claude_rates(catalog: Catalog, model: str, at: datetime | None) -> ClaudeRates | None:
    key = normalize_claude_model(model)
    if at is not None and key in CLAUDE_HISTORICAL and key in CLAUDE_BUILTIN:
        return CLAUDE_HISTORICAL[key] if at < CLAUDE_HISTORICAL_UNTIL else CLAUDE_BUILTIN[key]
    price = claude_models_dev_price(catalog, model)
    if price is not None:
        return _claude_rates_from_models_dev(price)
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
    input_tokens, cache_read, cache_write, output = (max(0, n) for n in (input_tokens, cache_read, cache_write, output))
    one_hour = min(max(0, cache_write_1h), cache_write)
    five_min = cache_write - one_hour
    r = rates
    long = r.threshold is not None and input_tokens + cache_read + cache_write > r.threshold

    def rate(normal: float, above: float | None) -> float:
        return above if long and above is not None else normal

    i_rate = rate(r.input, r.input_above)
    return (
        input_tokens * i_rate
        + cache_read * rate(r.cache_read, r.cache_read_above)
        + five_min * rate(r.cache_write, r.cache_write_above)
        + one_hour * i_rate * 2
        + output * rate(r.output, r.output_above)
    )


# -- Codex, CodexBar's rules --------------------------------------------------------------

CODEX_UNATTRIBUTED = "unknown"  # the model of a request whose turn named none: never priced
# From this time Codex Auto-review requests are free for ChatGPT sign-ins (2026-10-06 00:00 PDT).
CODEX_AUTO_REVIEW_FREE_FROM = datetime.fromtimestamp(1_791_270_000, timezone.utc)


def codex_models_dev_targets(model: str) -> list[tuple[str, str]]:
    """The (provider, model) pairs models.dev is asked for, in order (``codexModelsDevPricingTargets``):
    a bare id asks OpenAI for it raw, then normalized; a ``route/model`` id asks only its route."""
    t = model.strip()
    if not t:
        return []
    if "/" in t:
        return _route_targets(t)
    normalized = normalize_codex_model(t)
    return [("openai", t)] + ([("openai", normalized)] if normalized != t else [])


def codex_rates(catalog: Catalog, model: str, at: datetime | None) -> CodexRates | None:
    """``CostUsagePricing.resolvedCodexPricing``: rates before a repricing for older requests, else
    the first models.dev match (gaps filled from the bundled table when it is OpenAI's), else the
    bundled table, else no price."""
    key = normalize_codex_model(model)
    if key == CODEX_UNATTRIBUTED:
        return None
    if at is not None and key in CODEX_HISTORICAL and at < CODEX_HISTORICAL[key][0]:
        return CODEX_HISTORICAL[key][1]
    price = next((p for p in (catalog.price(*t) for t in codex_models_dev_targets(model)) if p is not None), None)
    if price is None:
        return CODEX_BUILTIN.get(key)
    bundled = CODEX_BUILTIN.get(key) if price.provider == "openai" else None
    # Without a long-context block models.dev has no opinion, so the bundled one applies; with
    # one, its gaps fall back to the normal rates rather than to another source.
    if price.threshold is None:
        bundled_long = bundled
        cache_read_above = _first(price.cache_read_above, getattr(bundled_long, "cache_read_above", None))
    else:
        bundled_long = None
        cache_read_above = _first(price.cache_read_above, price.cache_read, price.input_above, price.input)
    return CodexRates(
        input=price.input,
        output=price.output,
        cache_read=_first(price.cache_read, getattr(bundled, "cache_read", None)),
        threshold=_first(price.threshold, getattr(bundled, "threshold", None)),
        input_above=_first(price.input_above, getattr(bundled_long, "input_above", None)),
        output_above=_first(price.output_above, getattr(bundled_long, "output_above", None)),
        cache_read_above=cache_read_above,
    )


def codex_request_cost_usd(
    catalog: Catalog,
    model: str,
    input_tokens: int,
    cached: int,
    output: int,
    *,
    at: datetime | None = None,
    chatgpt_account: bool = False,
) -> float | None:
    """One Codex request at list price with CodexBar v0.78.0's rules; None when the model has no price.
    ``input_tokens`` includes ``cached``, as Codex reports it; ``output`` includes reasoning.
    ``at`` is the request time and ``chatgpt_account`` whether its session was created by a ChatGPT
    account (``session_meta.creator_account_id``): Auto-review requests are then free from
    ``CODEX_AUTO_REVIEW_FREE_FROM``, whatever the catalog says."""
    if (
        chatgpt_account
        and at is not None
        and at >= CODEX_AUTO_REVIEW_FREE_FROM
        and normalize_codex_model(model) == "codex-auto-review"
    ):
        return 0.0
    r = codex_rates(catalog, model, at)
    if r is None:
        return None
    total = max(0, input_tokens)
    cached = min(max(0, cached), total)
    if r.threshold is not None and total > r.threshold:
        input_rate = _first(r.input_above, r.input)
        cache_rate = _first(r.cache_read_above, r.cache_read, input_rate)
        output_rate = _first(r.output_above, r.output)
    else:
        input_rate, cache_rate, output_rate = r.input, _first(r.cache_read, r.input), r.output
    return (total - cached) * input_rate + cached * cache_rate + max(0, output) * output_rate


def _first(*rates: float | None) -> float | None:
    """The first rate that is set (Swift's ``a ?? b ?? c``)."""
    return next((r for r in rates if r is not None), None)
