"""Claude Code pricing with CodexBar v0.78.0's rules: built-in and historical rates, models.dev
lookup and its routing of non-Claude model ids. Ported from CodexBar's tests
(CostUsageClaudeResolverTests, CostUsageClaudeKimiAliasTests, CostUsagePricingClaudeThresholdTests,
CostUsagePricingTests, ModelsDevPricingTests)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tokenledger.models import DAY_TOTAL, CostQuality
from tokenledger.pricing import (
    CLAUDE_FIRST_PARTY,
    Catalog,
    claude_cost_usd,
    claude_models_dev_targets,
    normalize_claude_model,
)
from tokenledger.sources import SourceContext
from tokenledger.sources.claude_logs import ClaudeLogsSource

from conftest import utc
from test_platforms import entry, write

CUTOFF = datetime.fromtimestamp(1_773_360_000, timezone.utc)
AT = datetime(2026, 10, 19, tzinfo=timezone.utc)


def models(rows: dict) -> dict:
    return {"models": {key: {"id": key, "cost": cost} for key, cost in rows.items()}}


# CostUsageClaudeResolverTests.catalog()
RESOLVER = Catalog(providers={
    "anthropic": {"models": {
        "claude-test-known": {"id": "claude-test-known", "cost": {
            "input": 3, "output": 15, "cache_read": 0.3, "cache_write": 3.75,
            "context_over_200k": {"input": 6, "output": 22.5, "cache_read": 0.6, "cache_write": 7.5}}},
        "claude-test-zero": {"id": "claude-test-zero", "cost": {"input": 0, "output": 0}},
        "claude-test-null": {"id": "claude-test-null", "cost": {"input": None, "output": 1}},
        "claude-test-empty-context": {"id": "claude-test-empty-context", "cost": {
            "input": 4, "output": 8, "context_over_200k": {}}},
        "claude-test-partial-context": {"id": "claude-test-partial-context", "cost": {
            "input": 4, "output": 8, "context_over_200k": {"input": 9}}},
        "alias-key": {"id": " claude-test-id-fallback ", "cost": {"input": 7, "output": 11}},
        "claude-sonnet-4-5-20250929": {"id": "claude-sonnet-4-5-20250929", "cost": {"input": 41, "output": 42}},
        "claude-sonnet-4-5": {"id": "claude-sonnet-4-5", "cost": {"input": 2, "output": 3}},
        "claude-sonnet-4-6": {"id": "claude-sonnet-4-6", "cost": {"input": 99, "output": 100}},
        "claude-opus-4-6": {"id": "claude-opus-4-6", "cost": {"input": 99, "output": 100}},
        "example": {"id": "example", "cost": {"input": 2, "output": 3}},
        "collision": {"id": "collision", "cost": {"input": 1, "output": 2}},
    }},
    "openai": models({"gpt-fixture": {"input": 3, "output": 4}, "collision": {"input": 5, "output": 6}}),
    "google": models({"gemini-fixture": {"input": 7, "output": 8}}),
    "moonshot": models({"kimi-fixture": {"input": 9, "output": 10}}),
    "kimi-for-coding": models({"k3": {"input": 11, "output": 12}}),
    "minimax": models({"minimax-fixture": {"input": 13, "output": 14}}),
    "deepseek": models({"deepseek-fixture": {"input": 15, "output": 16}}),
    "opencode": models({"fixture": {"input": 17, "output": 18}}),
})
EMPTY = Catalog(providers={})


def cost(catalog: Catalog, model: str, *, inp=100, cr=0, cw=0, cw1h=0, out=0, at=AT) -> float | None:
    return claude_cost_usd(catalog, model, input_tokens=inp, cache_read=cr, cache_write=cw, cache_write_1h=cw1h,
                           output=out, at=at)


@pytest.mark.parametrize("model, rate", [
    ("claude-test-known", 3),
    ("claude-test-zero", 0),  # a zero price is a price
    ("claude-test-null", None),  # no input rate: not priceable
    ("claude-test-unknown", None),
    ("claude-test-id-fallback", 7),  # matched by the model's id, not its key
    (" \tclaude-test-known\n", 3),
    ("CLAUDE-test-known", None),  # ids are case-sensitive
    ("claude-sonnet-4-5", 2),
    ("claude-sonnet-4-5@20250929", 41),  # Vertex spelling of the dated snapshot
    ("anthropic.claude-sonnet-4-5-20250929-v1:0", 41),  # Bedrock: the dated snapshot before the base id
    ("claude-sonnet-4-5@default", 2),
    ("claude-sonnet-4-6", 3),  # built-in rates, never the catalog's
    ("anthropic/claude-sonnet-4-6", 99),  # a routed id is not the built-in model
    ("vertex/claude-sonnet-4-5", None),  # unknown route
    ("collision", None),  # listed by two vendors: ambiguous
    ("openai/collision", 5),
    ("unrecognized/collision", None),
    ("anthropic.anthropic.example", 2),  # one vendor lists it
    ("gpt-fixture", 3),
    ("gemini-fixture", 7),
    ("kimi-fixture", 9),
    ("minimax-fixture", 13),
    ("deepseek-fixture", 15),
    ("k3[1m]", 11),  # Claude Code's context-window alias of Kimi's k3
    ("kimi-coding/k3[1m]", 11),
    ("kimi-for-coding/k3", 11),
    ("opencode-free/fixture", 17),  # opencode-free falls back to opencode
    ("", None),
    (" \n", None),
])
def test_models_dev_routing(model, rate):
    expected = None if rate is None else 100 * (rate / 1e6)  # tokens x USD per token, as priced
    assert cost(RESOLVER, model) == expected


def test_catalog_thresholds_and_partial_context_rates():
    # context_over_200k without rates still sets the 200K boundary; missing rates keep the normal ones
    assert cost(RESOLVER, "claude-test-empty-context", inp=200_001) == pytest.approx(200_001 * 4e-6)
    assert cost(RESOLVER, "claude-test-partial-context", inp=200_001, out=10) == pytest.approx(200_001 * 9e-6 + 10 * 8e-6)
    assert cost(RESOLVER, "claude-test-known", inp=199_998, cr=1, cw=1) == pytest.approx(
        199_998 * 3e-6 + 0.3e-6 + 3.75e-6)
    assert cost(RESOLVER, "claude-test-known", inp=199_998, cr=1, cw=1, cw1h=1, out=7) == pytest.approx(
        199_998 * 3e-6 + 0.3e-6 + 3e-6 * 2 + 7 * 15e-6)
    assert cost(RESOLVER, "claude-test-known", inp=199_999, cr=1, cw=1) == pytest.approx(
        199_999 * 6e-6 + 0.6e-6 + 7.5e-6)
    # negative counts are treated as zero
    assert cost(RESOLVER, "claude-test-known", inp=-1, cr=-2, cw=-3, cw1h=-4, out=-5) == 0
    # a tiers entry with the same rates gives the boundary
    tiered = Catalog(providers={"anthropic": models({"claude-tiered": {
        "input": 1, "output": 2, "context_over_200k": {"input": 5, "output": 6},
        "tiers": [{"tier": {"type": "context", "size": 128_000}, "input": 5, "output": 6}]}})})
    assert cost(tiered, "claude-tiered", inp=128_001) == pytest.approx(128_001 * 5e-6)
    assert cost(tiered, "claude-tiered", inp=128_000) == pytest.approx(128_000 * 1e-6)


def test_historical_and_built_in_rates():
    # CostUsagePricingTests: opus/sonnet 4.6 bill a long-context tier only before 2026-03-13
    assert cost(EMPTY, "claude-sonnet-4-6", inp=240_000, at=CUTOFF - timedelta(seconds=1)) == 1.44
    assert cost(EMPTY, "claude-sonnet-4-6", inp=240_000, at=CUTOFF) == 0.72
    stale = Catalog(providers={"anthropic": models({"claude-sonnet-4-6": {
        "input": 3, "output": 15, "cache_read": 0.3, "cache_write": 3.75,
        "context_over_200k": {"input": 6, "output": 22.5, "cache_read": 0.6, "cache_write": 7.5}}})})
    assert cost(stale, "claude-sonnet-4-6", inp=240_000, at=CUTOFF) == 0.72
    assert cost(EMPTY, "claude-sonnet-4-6", inp=0, cw=240_000) == 240_000 * 3.75e-6
    # without a date the catalog wins
    assert cost(stale, "claude-sonnet-4-6", inp=200_010, cr=5, cw=5, out=5, at=None) == (
        200_010 * 6e-6 + 5 * 0.6e-6 + 5 * 7.5e-6 + 5 * 22.5e-6)
    assert cost(EMPTY, "claude-opus-4-6", inp=200_001, at=CUTOFF - timedelta(seconds=1)) == pytest.approx(200_001 * 1e-5)
    assert cost(EMPTY, "claude-opus-4-7", inp=10, out=5) == 10 * 5e-6 + 5 * 2.5e-5
    assert cost(EMPTY, "claude-opus-4-8", inp=10, out=5) == 10 * 5e-6 + 5 * 2.5e-5
    assert cost(EMPTY, "claude-opus-4-1-20250805", inp=10, out=5) is not None
    assert cost(EMPTY, "claude-opus-4-6-20260205", inp=10, out=5) is not None
    assert cost(EMPTY, "claude-fable-5", inp=100, cr=20, cw=10, out=5) == 100 * 1e-5 + 20 * 1e-6 + 10 * 1.25e-5 + 5 * 5e-5
    # 1-hour cache writes cost twice the input rate
    assert cost(EMPTY, "claude-fable-5", inp=100, cr=20, cw=30, cw1h=20, out=5) == (
        100 * 1e-5 + 20 * 1e-6 + 10 * 1.25e-5 + 20 * 2e-5 + 5 * 5e-5)
    assert cost(EMPTY, "glm-4.6", inp=100, cr=500, out=40) is None
    # the built-in long tier: whole request at the higher rates
    assert cost(EMPTY, "claude-sonnet-4-20250514", inp=150_000, cr=50_001) == pytest.approx(150_000 * 6e-6 + 50_001 * 6e-7)


def test_long_context_applies_to_both_cache_write_durations():
    catalog = Catalog(providers={"anthropic": models({"claude-threshold-model": {
        "input": 3, "output": 15, "cache_read": 0.3, "cache_write": 3.75,
        "context_over_200k": {"input": 6, "output": 22.5, "cache_read": 0.6, "cache_write": 7.5}}})})
    assert cost(catalog, "claude-threshold-model", inp=0, cw=240_000, cw1h=120_000) == 120_000 * 12e-6 + 120_000 * 7.5e-6


def threshold_catalog(provider="openai", model="gpt-5.6-sol") -> Catalog:
    return Catalog(providers={provider: {"id": provider, **models({model: {
        "input": 2, "output": 4, "cache_read": 0.25, "cache_write": 3,
        "context_over_200k": {"input": 7, "output": 11, "cache_read": 0.5, "cache_write": 9}}})}})


@pytest.mark.parametrize("total", [200_001, 271_999, 272_000, 272_001])
@pytest.mark.parametrize("cache_read", [0, 100_000])
def test_openai_models_keep_the_bundled_272k_boundary(total, cache_read):
    cw = 10_000
    inp = total - cache_read - cw
    expected = (
        inp * 7e-6 + cache_read * 0.5e-6 + cw * 9e-6 + 13 * 11e-6
        if total > 272_000
        else inp * 2e-6 + cache_read * 0.25e-6 + cw * 3e-6 + 13 * 4e-6
    )
    assert cost(threshold_catalog(), "gpt-5.6-sol", inp=inp, cr=cache_read, cw=cw, out=13) == pytest.approx(expected, abs=1e-10)


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_models_without_a_bundled_boundary_keep_the_catalog_boundary(provider):
    catalog = threshold_catalog(provider, "threshold-fixture")
    assert cost(catalog, f"{provider}/threshold-fixture", inp=100_001, cr=90_000, cw=10_000, out=13) == pytest.approx(
        100_001 * 7e-6 + 90_000 * 0.5e-6 + 10_000 * 9e-6 + 13 * 11e-6, abs=1e-10)
    huge = cost(catalog, f"{provider}/threshold-fixture", inp=2**63, cr=1, cw=1, out=13)
    assert huge == pytest.approx(2**63 * 7e-6 + 0.5e-6 + 9e-6 + 13 * 11e-6)


KIMI_RATES = {"input": 2, "output": 8, "cache_read": 0.5, "cache_write": 3}


def kimi_catalog(rows: dict) -> Catalog:
    providers = {p: {"id": p, **models(m)} for p, m in rows.items()}
    providers.setdefault("anthropic", {"id": "anthropic", "models": {}})["models"]["claude-test-pricing"] = {
        "id": "claude-test-pricing", "cost": {"input": 3, "output": 15}}
    providers.setdefault("openai", {"id": "openai", "models": {}})["models"]["gpt-test-pricing"] = {
        "id": "gpt-test-pricing", "cost": {"input": 1, "output": 4}}
    return Catalog(providers=providers)


def test_kimi_alias_prefers_exact_routes_and_never_guesses_another_vendor():
    catalog = kimi_catalog({
        "kimi-coding": {"k3": {"input": 91, "output": 92}},
        "kimi-for-coding": {"k3": KIMI_RATES, "k3[1m]": {"input": 7, "output": 11}, "k3-256k": {"input": 13, "output": 17}},
        "openai": {"k3": {"input": 97, "output": 98}, "k3[1m]": {"input": 19, "output": 23}},
    })
    for model in ("k3[1m]", "kimi-coding/k3[1m]", "kimi-for-coding/k3[1m]"):
        assert cost(catalog, model, inp=1) == 7e-6
    assert cost(catalog, "openai/k3[1m]", inp=1) == 19e-6
    assert cost(catalog, "k3-256k", inp=1) == 13e-6
    exact = kimi_catalog({"kimi-coding": {"k3[1m]": {"input": 29, "output": 31}},
                          "kimi-for-coding": {"k3[1m]": {"input": 7, "output": 11}, "k3": KIMI_RATES}})
    assert cost(exact, "kimi-coding/k3[1m]", inp=1) == 29e-6

    catalog = kimi_catalog({
        "kimi-for-coding": {"k3": KIMI_RATES},
        "openai": {"k3": {"input": 97, "output": 98}, "k3[1m]": {"input": 19, "output": 23}},
        "moonshotai": {"k3": {"input": 41, "output": 43}},
    })
    assert cost(catalog, "k3[1m]", inp=1) == 2e-6
    for model in ("k3[2m]", "k3-256k", "kimi-k3[1m]", "anthropic/k3[1m]", "unknown/k3[1m]"):
        assert cost(catalog, model, inp=1) is None
    non_kimi = kimi_catalog({"openai": {"k3[1m]": {"input": 19, "output": 23}}, "moonshot": {"k3": {"input": 41, "output": 43}}})
    assert cost(non_kimi, "k3[1m]", inp=1) is None
    assert cost(kimi_catalog({"openai": {"k3": KIMI_RATES}}), "openai/k3[1m]", inp=1) is None
    assert normalize_claude_model("k3[1m]") == "k3[1m]"  # the reported model name stays as logged


@pytest.mark.parametrize("model", ["k3[1m]", "kimi-coding/k3[1m]", "kimi-for-coding/k3[1m]"])
def test_kimi_alias_zero_and_missing_rates(model):
    assert cost(kimi_catalog({"kimi-for-coding": {"k3": {"input": 0, "output": 0}}}), model, inp=1) == 0
    assert cost(kimi_catalog({"kimi-for-coding": {"k3": {"output": 0}}}), model, inp=1) is None
    assert cost(kimi_catalog({"kimi-for-coding": {"k3": {"input": None, "output": 0}}}), model, inp=1) is None


@pytest.mark.parametrize("inp", [199_950, 199_951])
def test_kimi_alias_long_context(inp):
    rates = {**KIMI_RATES, "context_over_200k": {"input": 4, "output": 16, "cache_read": 1, "cache_write": 6}}
    multiplier = 2 if inp + 50 > 200_000 else 1
    expected = (inp * 2 + 20 * 0.5 + 25 * 3 + 5 * 4 + 10 * 8) * multiplier / 1e6
    assert cost(kimi_catalog({"kimi-for-coding": {"k3": rates}}), "k3[1m]", inp=inp, cr=20, cw=30, cw1h=5, out=10) == (
        pytest.approx(expected, abs=1e-12))


def test_bare_ids_go_to_their_vendor_and_routes_never_cross_charge():
    catalog = Catalog(providers={
        "anthropic": {"id": "anthropic", **models({"claude-sonnet-4-6": {"input": 3, "output": 15},
                                                  "deepseek-v4-flash": {"input": 99, "output": 199}})},
        "openai": {"id": "openai", **models({"claude-sonnet-4-6": {"input": 1, "output": 2}})},
        "deepseek": {"id": "deepseek", **models({"deepseek-v4-flash": {"input": 0.14, "output": 0.28}})},
        "opencode-go": {"id": "opencode-go", **models({"deepseek-v4-flash": {"input": 0.07, "output": 0.14}})},
    })
    assert cost(catalog, "deepseek-v4-flash", out=5) == 100 * 0.14e-6 + 5 * 0.28e-6
    assert cost(catalog, "opencode-go/deepseek-v4-flash", out=5) == 100 * 0.07e-6 + 5 * 0.14e-6
    assert cost(catalog, "claude-sonnet-4-6", inp=10, out=5, at=None) == 10 * 3e-6 + 5 * 15e-6
    assert claude_models_dev_targets("deepseek-v4-flash")[0][0] == "deepseek"
    assert claude_models_dev_targets("claude-sonnet-4-6")[0][0] == "anthropic"
    assert set(CLAUDE_FIRST_PARTY) >= {"anthropic", "openai", "google", "moonshot", "kimi-for-coding", "minimax", "deepseek"}

    only_deepseek = Catalog(providers={"deepseek": {"id": "deepseek", **models({"deepseek-v4-flash": {"input": 0.14, "output": 0.28}})}})
    assert cost(only_deepseek, "opencode-go/deepseek-v4-flash", out=5) is None
    assert cost(only_deepseek, "unknown-route/deepseek-v4-flash", out=5) is None

    shared = Catalog(providers={"openai": {"id": "openai", **models({"shared-model": {"input": 1, "output": 2}})},
                                "google": {"id": "google", **models({"shared-model": {"input": 3, "output": 4}})}})
    assert cost(shared, "shared-model", out=5) is None


def test_targets():
    # ModelsDevPricingTargetResolver: the route prefix is dropped once; aliases follow the exact route
    assert claude_models_dev_targets("OpenAI/GPT-5") == [("openai", "GPT-5")]
    assert claude_models_dev_targets("openai/openai/gpt-5") == [("openai", "openai/gpt-5"), ("openai", "gpt-5")]
    assert claude_models_dev_targets("kimi-coding/k3") == [("kimi-coding", "k3"), ("kimi-for-coding", "k3")]
    assert claude_models_dev_targets("opencode-free/opencode") == [("opencode-free", "opencode"), ("opencode", "opencode")]
    assert claude_models_dev_targets("openai/gpt-5.6") == [("openai", "gpt-5.6"), ("openai", "gpt-5.6-sol")]
    assert claude_models_dev_targets("kimi-coding/k3[1m]") == [
        ("kimi-coding", "k3[1m]"), ("kimi-for-coding", "k3[1m]"), ("kimi-for-coding", "k3")]
    assert claude_models_dev_targets("gemini-2.5-pro") == [("google", "gemini-2.5-pro")]
    assert claude_models_dev_targets("o3") == [("openai", "o3")]
    assert claude_models_dev_targets("kimi-k2") == [("moonshot", "kimi-k2"), ("kimi-for-coding", "kimi-k2")]
    assert claude_models_dev_targets("claude-opus-4-1-20250805") == [
        ("anthropic", "claude-opus-4-1-20250805"), ("anthropic", "claude-opus-4-1")]
    assert claude_models_dev_targets("mystery") == [(p, "mystery") for p in CLAUDE_FIRST_PARTY]
    for model in ("", "openai/", "unknown/x", "/gpt-5"):
        assert claude_models_dev_targets(model) == []


# -- the scanner prices the logged id, then reports with the normalized id ------------------


def test_report_reprices_with_the_normalized_model(tmp_path, settings):
    """CostUsageClaudeResolverTests: a dated id scanned at the snapshot's rate is reported at
    the rate of the model name it is reported under; a scan price below half a billionth of a
    dollar stays zero; a report without a price keeps the scan price."""
    catalog = Catalog(providers={"anthropic": models({
        "claude-sonnet-4-5-20250929": {"input": 41, "output": 42},
        "claude-sonnet-4-5": {"input": 2, "output": 3},
        "claude-haiku-4-5-20251001": {"input": 0.0001, "output": 1},
        "claude-haiku-4-5": {"input": 1, "output": 5},
        "claude-zz-v1:0": {"input": 3, "output": 15},
        "claude-test-zero": {"input": 0, "output": 0},
    })})
    root = tmp_path / "projects"
    write(root / "p" / "s.jsonl", [
        entry("2026-10-19T10:00:00Z", "m1", "r1", model="claude-sonnet-4-5-20250929", inp=100, cw=0, cw1h=0, cr=0, out=0),
        entry("2026-10-19T10:00:01Z", "m2", "r2", model="claude-sonnet-4-5", inp=100, cw=0, cw1h=0, cr=0, out=0),
        entry("2026-10-19T10:00:02Z", "m3", "r3", model="claude-haiku-4-5-20251001", inp=1, cw=0, cw1h=0, cr=0, out=0),
        entry("2026-10-19T10:00:03Z", "m4", "r4", model="us.anthropic.claude-zz-v1:0", inp=100, cw=0, cw1h=0, cr=0, out=0),
        entry("2026-10-19T10:00:04Z", "m5", "r5", model="claude-test-zero", inp=100, cw=0, cw1h=0, cr=0, out=0),
    ])
    ctx = SourceContext(settings=settings, now_utc=utc("2026-10-20T10:00:00"))
    [result] = ClaudeLogsSource(roots=[root], catalog=catalog).collect(ctx)
    rows = {r.model: r for r in result.rows if r.day == "2026-10-19"}
    assert rows["claude-sonnet-4-5"].cost_micros == 400  # 2 x 100 tokens at $2/M
    assert rows["claude-haiku-4-5"].cost_micros == 0
    assert rows["claude-zz"].cost_micros == 300
    assert rows["claude-test-zero"].cost_micros == 0
    assert rows["claude-test-zero"].cost_quality == CostQuality.LIST_PRICE
    assert rows[DAY_TOTAL].cost_micros == 700
