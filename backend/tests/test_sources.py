from __future__ import annotations

import json

import pytest

from tokenledger.models import DAY_TOTAL, CostQuality, Scope
from tokenledger.sources import SourceContext, SourceError
from tokenledger.sources.codex_server import parse_usage_response
from tokenledger.sources.codexbar import parse_cost_export
from tokenledger.sources.codexbar_quota import parse_history_file

from conftest import FIXTURES, utc


@pytest.fixture
def ctx(settings):
    return SourceContext(settings=settings, now_utc=utc("2026-09-27T10:00:00"))


def load(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def by_key(result):
    return {(r.day, r.model): r for r in result.rows}


def test_claude_export_normalized(ctx):
    result = parse_cost_export(load("codexbar-claude.json"), provider="claude", ctx=ctx, tool_version="CodexBar 0.60.4")
    rows = by_key(result)
    day = rows[("2026-08-01", DAY_TOTAL)]
    assert (day.input_uncached, day.cache_read, day.cache_write, day.output, day.total) == (100, 1000, 200, 50, 1350)
    assert day.cost_micros == 1_250_000 and day.cost_quality == CostQuality.LIST_PRICE
    assert rows[("2026-09-27", "claude-sonnet-5")].total == 55
    assert result.series.scope == Scope.MACHINE and result.series.machine == "test-mac"
    assert (result.window_start, result.window_end) == ("2025-09-28", "2026-09-27")


def test_codex_export_moves_cached_out_of_input(ctx):
    result = parse_cost_export(load("codexbar-codex.json"), provider="codex", ctx=ctx, tool_version="v")
    rows = by_key(result)
    day = rows[("2026-09-26", DAY_TOTAL)]
    assert (day.input_uncached, day.cache_read, day.output, day.reasoning, day.total) == (100, 900, 20, 5, 1020)
    unpriced = rows[("2026-09-26", "codex-auto-review")]
    assert unpriced.cost_micros is None and unpriced.cost_quality == CostQuality.UNPRICED


def test_inconsistent_totals_fail(ctx):
    payload = json.loads(load("codexbar-claude.json"))
    payload[0]["daily"][0]["totalTokens"] += 1
    with pytest.raises(SourceError, match="add up"):
        parse_cost_export(json.dumps(payload).encode(), provider="claude", ctx=ctx, tool_version="v")


def test_incomplete_scan_with_data_fails(ctx):
    payload = json.loads(load("codexbar-claude.json"))
    payload[0]["historyCoverageIsEstablished"] = False
    with pytest.raises(SourceError, match="incomplete"):
        parse_cost_export(json.dumps(payload).encode(), provider="claude", ctx=ctx, tool_version="v")


def test_empty_unestablished_provider_is_skipped(ctx):
    raw = json.dumps([{"provider": "antigravity", "daily": [], "historyCoverageIsEstablished": False}]).encode()
    assert parse_cost_export(raw, provider="antigravity", ctx=ctx, tool_version="v") is None


def test_error_report_fails(ctx):
    raw = json.dumps([{"provider": "cli", "error": {"message": "boom"}}]).encode()
    with pytest.raises(SourceError, match="boom"):
        parse_cost_export(raw, provider="claude", ctx=ctx, tool_version="v")


def test_codex_server_buckets():
    response = json.loads(load("codex-server-usage.json"))
    result = parse_usage_response(response, raw=b"{}", tool_version="codex 1")
    assert [(r.day, r.total) for r in result.rows] == [("2026-02-26", 1000), ("2026-09-26", 2000)]
    assert result.series.scope == Scope.ACCOUNT and result.series.machine == "account"
    assert (result.window_start, result.window_end) == ("2026-02-26", "2026-09-26")
    assert result.meta["summary"]["lifetimeTokens"] == 3000


def test_codex_server_error():
    with pytest.raises(SourceError):
        parse_usage_response({"id": 1, "error": {"message": "nope"}}, raw=b"", tool_version="v")


def test_quota_history():
    samples = parse_history_file("claude", load("history-claude.json"))
    assert len(samples) == 3
    s = samples[1]
    assert (s.provider, s.account, s.window, s.window_minutes, s.used_percent) == ("claude", "acct", "session", 300, 40.0)
