"""Codex request pricing (``pricing.codex_request_cost_usd``), ported from CodexBar v0.78.0's tests
(``CostUsagePricingTests``, ``CodexSolHistoricalPricingTests``, ``CodexAliasedModelPricingTests``,
``CostUsageAstraPricingTests``, ``CostUsageCodexAutoReviewTests``). Swift's per-token rates are
USD per token; the catalogs here are models.dev's USD per million."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from tokenledger.models import DAY_TOTAL, CostQuality
from tokenledger.pricing import (
    CODEX_AUTO_REVIEW_FREE_FROM,
    Catalog,
    codex_models_dev_targets,
    codex_request_cost_usd,
    normalize_codex_model,
)
from tokenledger.sources import SourceContext
from tokenledger.sources.codex_logs import CodexLogsSource

from conftest import utc
from test_sources import record, turn, u, write_log

EMPTY = Catalog(providers={})
SOL_CUTOFF = datetime(2026, 8, 21, tzinfo=timezone.utc)


def catalog(models: dict, provider: str = "openai") -> Catalog:
    return Catalog(providers={provider: {"id": provider, "models": {m: {"id": m, "cost": c} for m, c in models.items()}}})


def cost(cat: Catalog, model: str, inp: int, cached: int, out: int, **kw) -> float | None:
    return codex_request_cost_usd(cat, model, inp, cached, out, **kw)


def test_normalizes_codex_model_variants_exactly():
    cases = {
        "openai/gpt-5-codex": "gpt-5-codex",
        "gpt-5.1-codex-max": "gpt-5.1-codex-max",
        "gpt-5.4-pro-2026-03-05": "gpt-5.4-pro",
        "gpt-5.5-2026-04-23": "gpt-5.5",
        "gpt-5.3-codex-spark": "gpt-5.3-codex-spark",
        "openai/gpt-5.6-sol": "gpt-5.6-sol",
        "gpt-5.6": "gpt-5.6-sol",
        "gpt-reserve": "gpt-5.6-luna",
        "openai/gpt-reserve": "gpt-5.6-luna",
        "gpt-daybreak-blue-latest": "gpt-5.6-sol",
        "gpt-daybreak-red-latest": "gpt-5.6-cyber",
        "gpt-5.6-sol-2099-01-01": "gpt-5.6-sol",
        "openai/gpt-5.6-terra-2099-01-01": "gpt-5.6-terra",
        "gpt-6.1-sol": "gpt-6.1-sol",
        "gpt-unlisted-2099-01-01": "gpt-unlisted-2099-01-01",  # dates are dropped only for bundled models
    }
    assert {raw: normalize_codex_model(raw) for raw in cases} == cases


def test_unattributed_usage_stays_unpriced_despite_a_catalog_collision():
    assert cost(catalog({"unknown": {"input": 99, "output": 199}}), "unknown", 100, 10, 5) is None


def test_provider_qualified_routes_price_from_their_route_only():
    cat = Catalog(providers={
        "deepseek": {"id": "deepseek", "models": {"deepseek-v4-flash": {"id": "deepseek-v4-flash", "cost": {"input": 0.14, "output": 0.28}}}},
        "kimi-for-coding": {"id": "kimi-for-coding", "models": {"k3": {"id": "k3", "cost": {"input": 0, "output": 0}}}},
        "opencode": {"id": "opencode", "models": {"deepseek-v4-flash-free": {"id": "deepseek-v4-flash-free", "cost": {"input": 0, "output": 0}}}},
        "opencode-go": {"id": "opencode-go", "models": {"deepseek-v4-flash": {"id": "deepseek-v4-flash", "cost": {"input": 0.07, "output": 0.14}}}},
        "openai": {"id": "openai", "models": {"deepseek-v4-flash": {"id": "deepseek-v4-flash", "cost": {"input": 99, "output": 199}}}},
        "unlisted-route": {"id": "unlisted-route", "models": {"deepseek-v4-flash": {"id": "deepseek-v4-flash", "cost": {"input": 1, "output": 1}}}},
    })
    assert cost(cat, "opencode-go/deepseek-v4-flash", 100, 10, 5) == 100 * 0.07e-6 + 5 * 0.14e-6
    assert cost(cat, "opencode-free/deepseek-v4-flash-free", 100, 10, 5) == 0  # falls back to opencode
    assert cost(cat, "kimi-coding/k3", 100, 10, 5) == 0  # falls back to kimi-for-coding
    assert cost(cat, "deepseek/deepseek-v4-flash", 100, 10, 5) == 100 * 0.14e-6 + 5 * 0.28e-6
    assert cost(cat, "unlisted-route/deepseek-v4-flash", 100, 0, 5) is None  # never another provider
    assert codex_models_dev_targets("openai/gpt-5.6") == [("openai", "gpt-5.6"), ("openai", "gpt-5.6-sol")]
    assert codex_models_dev_targets("gpt-5.6") == [("openai", "gpt-5.6"), ("openai", "gpt-5.6-sol")]


def test_bundled_fallback_without_a_catalog_entry():
    assert cost(EMPTY, "openai/gpt-5.5-2026-04-23", 100, 10, 5) == 90 * 5e-6 + 10 * 5e-7 + 5 * 3e-5
    sol = 90 * 4e-6 + 10 * 4e-7 + 5 * 2e-5
    assert cost(EMPTY, "openai/gpt-5.6-sol", 100, 10, 5) == sol
    assert cost(EMPTY, "gpt-5.6", 100, 10, 5) == sol
    assert cost(EMPTY, "gpt-daybreak-blue-latest", 100, 10, 5) == sol
    assert cost(EMPTY, "gpt-5.6-terra", 100, 10, 5) == 90 * 2e-6 + 10 * 2e-7 + 5 * 1.2e-5
    luna = 90 * 2e-7 + 10 * 2e-8 + 5 * 1.2e-6
    assert cost(EMPTY, "gpt-5.6-luna", 100, 10, 5) == luna
    assert cost(EMPTY, "gpt-reserve", 100, 10, 5) == luna
    cyber = 90 * 1.25e-5 + 10 * 1.25e-6 + 5 * 7.5e-5
    assert cost(EMPTY, "gpt-5.6-cyber", 100, 10, 5) == cyber
    assert cost(EMPTY, "gpt-5.5-cyber", 100, 10, 5) == cyber
    assert cost(EMPTY, "gpt-daybreak-red-latest", 100, 10, 5) == cyber
    # no cache-read rate: cached input costs the input rate
    assert cost(EMPTY, "openai/gpt-5.5-pro-2026-04-23", 100, 10, 5) == 100 * 3e-5 + 5 * 1.8e-4
    assert cost(EMPTY, "gpt-5.3-codex-spark", 100, 10, 5) == 0  # research preview
    for model in ("gpt-5.1-codex-max", "gpt-5.3-codex", "gpt-5.4-mini-2026-03-17", "gpt-5.4-nano"):
        assert cost(EMPTY, model, 100, 10, 5) is not None
    for model in ("gpt-6", "other-provider/gpt-6-astra", "fixture-unknown-model"):
        assert cost(EMPTY, model, 1000, 0, 100) is None


@pytest.mark.parametrize("model", ["gpt-6-astra", "openai/gpt-6-astra", "gpt-6-astra-2099-01-01"])
def test_astra_fallback(model):
    assert cost(EMPTY, model, 1000, 300, 100) == pytest.approx(700 * 10e-6 + 300 * 1e-6 + 100 * 50e-6, abs=1e-12)


@pytest.mark.parametrize("inp", [272_000, 272_001])
def test_astra_switches_the_entire_request_at_the_long_context_boundary(inp):
    long = inp > 272_000
    expected = ((inp - 100_000) * 10e-6 + 100_000 * 1e-6) * (2 if long else 1) + 1000 * 50e-6 * (1.5 if long else 1)
    assert cost(EMPTY, "gpt-6-astra", inp, 100_000, 1000) == pytest.approx(expected, abs=1e-12)


def test_bundled_long_context_rates_apply_to_the_whole_request():
    assert cost(EMPTY, "gpt-5.4", 272_001, 0, 10) == 272_001 * 5e-6 + 10 * 2.25e-5
    assert cost(EMPTY, "gpt-5.5", 272_001, 0, 10) == 272_001 * 1e-5 + 10 * 4.5e-5
    assert cost(EMPTY, "gpt-5.5", 272_000, 0, 128_000) == 272_000 * 5e-6 + 128_000 * 3e-5  # at the boundary
    assert cost(EMPTY, "gpt-5.5", 300_000, 200_000, 10) == 200_000 * 1e-6 + 100_000 * 1e-5 + 10 * 4.5e-5
    assert cost(EMPTY, "gpt-5.6-sol", 272_001, 10, 10) == 271_991 * 8e-6 + 10 * 8e-7 + 10 * 3e-5
    assert cost(EMPTY, "gpt-5.6-terra", 272_001, 10, 10) == 271_991 * 4e-6 + 10 * 4e-7 + 10 * 1.8e-5
    assert cost(EMPTY, "gpt-5.6-luna", 272_001, 10, 10) == 271_991 * 4e-7 + 10 * 4e-8 + 10 * 1.8e-6


def test_cached_input_is_part_of_input_and_clamped_to_it():
    assert cost(EMPTY, "gpt-5-codex", 1000, 900, 10) == 100 * 1.25e-6 + 900 * 1.25e-7 + 10 * 1e-5
    assert cost(EMPTY, "gpt-5.5", 20, 500, 5) == 20 * 5e-7 + 5 * 3e-5


def test_models_dev_wins_over_the_bundled_table():
    cat = catalog({"gpt-5.5": {"input": 10, "output": 20, "cache_read": 1}})
    assert cost(cat, "openai/gpt-5.5", 100, 10, 5) == 90 * 10e-6 + 10 * 1e-6 + 5 * 20e-6
    spark = catalog({"gpt-5.3-codex-spark": {"input": 2, "output": 8, "cache_read": 0.2}})
    assert cost(spark, "gpt-5.3-codex-spark", 100, 10, 5) == 90 * 2e-6 + 10 * 0.2e-6 + 5 * 8e-6
    # only another provider lists it: the bundled OpenAI rates apply
    other = catalog({"gpt-5.5": {"input": 10, "output": 20, "cache_read": 1}}, provider="anthropic")
    assert cost(other, "openai/gpt-5.5", 100, 10, 5) == 90 * 5e-6 + 10 * 5e-7 + 5 * 3e-5


def test_the_gpt56_alias_falls_back_to_canonical_sol_pricing():
    canonical = catalog({"gpt-5.6-sol": {"input": 7, "output": 31}})
    both = catalog({"gpt-5.6": {"input": 3, "output": 13}, "gpt-5.6-sol": {"input": 7, "output": 31}})
    assert cost(canonical, "gpt-5.6", 100, 0, 0) == 100 * 7e-6
    assert cost(both, "gpt-5.6", 100, 0, 0) == 100 * 3e-6


def test_models_dev_long_context_uses_the_openai_272k_threshold():
    cat = catalog({"gpt-5.5": {"input": 5, "output": 30, "cache_read": 0.5,
                               "context_over_200k": {"input": 10, "output": 45, "cache_read": 1}}})
    assert cost(cat, "gpt-5.5", 272_000, 0, 10) == 272_000 * 5e-6 + 10 * 3e-5
    assert cost(cat, "gpt-5.5", 272_001, 0, 10) == 272_001 * 1e-5 + 10 * 4.5e-5


@pytest.mark.parametrize("model", ["gpt-6.1-sol", "gpt-6-sol", "gpt-6-luna", "gpt-6-astra"])
def test_gpt6_models_switch_at_272k_like_openai_publishes(model):
    # models.dev names the block context_over_200k; OpenAI's pricing page sets the boundary at 272K
    cat = catalog({model: {"input": 2, "output": 10, "cache_read": 0.1,
                           "context_over_200k": {"input": 4, "output": 15, "cache_read": 0.2}}})
    assert cost(cat, model, 250_000, 200_000, 1000) == 50_000 * 2e-6 + 200_000 * 0.1e-6 + 1000 * 10e-6
    assert cost(cat, model, 272_001, 200_000, 1000) == 72_001 * 4e-6 + 200_000 * 0.2e-6 + 1000 * 15e-6


def test_other_models_switch_at_200k_unless_a_tier_says_otherwise():
    block = {"input": 10, "output": 45}
    cat = catalog({"threshold-model": {"input": 5, "output": 30, "context_over_200k": block}})
    assert cost(cat, "threshold-model", 200_000, 0, 100) == 200_000 * 5e-6 + 100 * 30e-6
    assert cost(cat, "threshold-model", 200_001, 0, 100) == 200_001 * 10e-6 + 100 * 45e-6
    tiered = catalog({"threshold-model": {"input": 5, "output": 30, "context_over_200k": block,
                                          "tiers": [{"tier": {"type": "context", "size": 128_000}, **block}]}})
    assert cost(tiered, "threshold-model", 128_001, 0, 100) == 128_001 * 10e-6 + 100 * 45e-6


def test_a_long_context_block_without_cache_read_uses_the_long_input_rate():
    cat = catalog({"gpt-5.5": {"input": 5, "output": 30, "context_over_200k": {"input": 10, "output": 45}}})
    assert cost(cat, "gpt-5.5", 300_000, 200_000, 10) == 100_000 * 10e-6 + 200_000 * 10e-6 + 10 * 45e-6


def test_bundled_rates_fill_only_what_the_catalog_omits():
    missing = catalog({"gpt-5.6-sol": {"input": 4, "output": 20}})
    explicit_zero = catalog({"gpt-5.6-sol": {"input": 4, "output": 20, "cache_read": 0, "cache_write": 0}})
    assert cost(missing, "gpt-5.6-sol", 100, 10, 0) == 90 * 4e-6 + 10 * 4e-7
    assert cost(explicit_zero, "gpt-5.6-sol", 100, 10, 0) == 90 * 4e-6
    # no long-context block: the bundled 272K threshold and long rates apply
    short_only = catalog({"gpt-5.6-sol": {"input": 4, "output": 20, "cache_read": 0.4}})
    assert cost(short_only, "gpt-5.6-sol", 272_001, 0, 10) == 272_001 * 8e-6 + 10 * 3e-5
    full = catalog({"gpt-5.6-sol": {"input": 1, "output": 2, "cache_read": 0.1, "cache_write": 1.25,
                                    "context_over_200k": {"input": 11, "output": 22, "cache_read": 1.1, "cache_write": 13.75}}})
    assert cost(full, "gpt-5.6-sol", 272_001, 100_000, 10) == 172_001 * 11e-6 + 100_000 * 1.1e-6 + 10 * 22e-6


@pytest.mark.parametrize("model", ["gpt-5.6-sol", "gpt-5.6"])
@pytest.mark.parametrize("inp", [100, 272_001])
@pytest.mark.parametrize("cat", [catalog({"gpt-5.6-sol": {"input": 4, "cache_read": 0.4, "cache_write": 5, "output": 20}}), EMPTY])
def test_sol_keeps_historical_rates_before_its_august_repricing(model, inp, cat):
    long = inp > 272_000
    for at, historical in ((SOL_CUTOFF - timedelta(seconds=1), True), (SOL_CUTOFF, False)):
        i = (10 if long else 5) if historical else (8 if long else 4)
        o = (45 if long else 30) if historical else (30 if long else 20)
        expected = ((inp - 10) * i + 10 * i / 10 + 5 * o) / 1e6
        assert cost(cat, model, inp, 10, 5, at=at) == pytest.approx(expected, abs=1e-12)
    assert cost(cat, model, inp, 10, 5) == cost(cat, model, inp, 10, 5, at=SOL_CUTOFF)  # undated: current


def test_terra_and_luna_keep_historical_rates_before_july_30():
    cutoff = datetime(2026, 7, 30, tzinfo=timezone.utc)
    before = cutoff - timedelta(seconds=1)
    assert cost(EMPTY, "gpt-5.6-terra", 100, 0, 10, at=before) == pytest.approx(100 * 2.5e-6 + 10 * 1.5e-5)
    assert cost(EMPTY, "gpt-5.6-terra", 100, 0, 10, at=cutoff) == pytest.approx(100 * 2e-6 + 10 * 1.2e-5)
    assert cost(EMPTY, "gpt-5.6-luna", 100, 0, 10, at=before) == pytest.approx(100 * 1e-6 + 10 * 6e-6)
    assert cost(EMPTY, "gpt-5.6-sol", 100, 0, 10, at=before) == pytest.approx(100 * 5e-6 + 10 * 3e-5)


def test_auto_review_is_free_only_for_chatgpt_sessions_from_the_cutoff():
    cat = catalog({"codex-auto-review": {"input": 1, "output": 2}})
    after, before = CODEX_AUTO_REVIEW_FREE_FROM, CODEX_AUTO_REVIEW_FREE_FROM - timedelta(seconds=1)
    for model in ("codex-auto-review", "openai/codex-auto-review"):
        assert cost(cat, model, 1000, 200, 100, at=after, chatgpt_account=True) == 0
        assert cost(EMPTY, model, 1000, 200, 100, at=after, chatgpt_account=True) == 0
        assert cost(EMPTY, model, 1000, 200, 100, at=before, chatgpt_account=True) is None
        assert cost(EMPTY, model, 1000, 200, 100, at=after) is None  # API-key sign-in
    # without the waiver it is priced like any other model
    assert cost(cat, "codex-auto-review", 1000, 0, 10, at=before, chatgpt_account=True) == 1000 * 1e-6 + 10 * 2e-6


# -- through the codex-logs reader -----------------------------------------------------------


@pytest.fixture
def ctx(settings):
    return SourceContext(settings=settings, now_utc=utc("2026-10-08T10:00:00"))


def session_meta(ts, creator):
    payload = {"id": "thread"} | ({"creator_account_id": creator} if creator is not None else {})
    return {"timestamp": ts, "type": "session_meta", "payload": payload}


def test_codex_logs_waive_auto_review_by_the_files_own_session(tmp_path, ctx):
    (tmp_path / "catalog.json").write_text(json.dumps({"fetchedAt": "2026-10-07T00:00:00Z", "catalog": {"providers": {}}}))
    home = tmp_path / "codex"
    ts = "2026-10-07T10:00:00Z"
    write_log(home, "rollout-chatgpt.jsonl", [
        session_meta(ts, "account-fixture"),
        session_meta(ts, None),  # a parent's metadata copied into a fork does not decide
        turn(ts, "t1", "codex-auto-review"), record(ts, "resp_a", "t1", u(1000, 200, 100)),
    ])
    write_log(home, "rollout-apikey.jsonl", [
        session_meta(ts, " \n"),
        turn(ts, "t2", "codex-auto-review"), record(ts, "resp_b", "t2", u(1000, 200, 100)),
    ])
    [result] = CodexLogsSource(home, tmp_path / "catalog.json").collect(ctx)
    raw = json.loads(result.raw)
    assert raw["days"]["2026-10-07"]["codex-auto-review"] == {
        "requests": 2, "input": 2000, "cached": 400, "output": 200, "reasoning": 0, "cost_micros": 0, "unpriced": 1}
    day = {(r.day, r.model): r for r in result.rows}[("2026-10-07", DAY_TOTAL)]
    assert day.cost_micros == 0 and day.cost_quality == CostQuality.LIST_PRICE
