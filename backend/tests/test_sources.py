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


def test_codex_binary_uses_first_existing_candidate(tmp_path):
    from tokenledger.sources.codex_server import find_binary

    old = tmp_path / "old-codex"
    new = tmp_path / "new-codex"
    old.write_text("#!/bin/sh\n")
    old.chmod(0o755)
    assert find_binary((str(new), str(old))) == str(old)
    new.write_text("#!/bin/sh\n")
    new.chmod(0o755)
    assert find_binary((str(new), str(old))) == str(new)
    with pytest.raises(SourceError, match="looked at"):
        find_binary((str(tmp_path / "missing"),), fallback=None)


def test_codex_binary_falls_back_to_path(tmp_path, monkeypatch):
    from tokenledger.sources.codex_server import find_binary

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    codex = bin_dir / "codex"
    codex.write_text("#!/bin/sh\n")
    codex.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    assert find_binary((str(tmp_path / "missing"),)) == str(codex)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    with pytest.raises(SourceError, match="codex on PATH"):
        find_binary((str(tmp_path / "missing"),))


# ---- codex-logs ----

from tokenledger.sources.codex_logs import CodexLogsSource, parse_logs_export

CODEX_CATALOG = {
    "fetchedAt": "2026-10-03T16:00:00Z",
    "catalog": {"providers": {"openai": {"id": "openai", "models": {"gpt-6-x": {"id": "gpt-6-x", "cost": {
        "input": 2, "cache_read": 0.2, "output": 10,
        "context_over_200k": {"input": 4, "cache_read": 0.4, "output": 15}}}}}}},
}


def u(inp, cached, out, reasoning=0):
    return {"input_tokens": inp, "cached_input_tokens": cached, "output_tokens": out,
            "reasoning_output_tokens": reasoning, "total_tokens": inp + out}


def turn(ts, turn_id, model="gpt-6-x"):
    return {"timestamp": ts, "type": "turn_context", "payload": {"turn_id": turn_id, "model": model}}


def count(ts, total, last):
    return {"timestamp": ts, "type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": total, "last_token_usage": last}}}


def record(ts, response_id, turn_id, usage):
    return {"timestamp": ts, "type": "token_usage_record", "payload": {"response_id": response_id, "turn_id": turn_id, "usage": usage}}


def write_log(home, name, entries, tail=""):
    path = home / "sessions/2026/10/04" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(e) + "\n" for e in entries) + tail)


@pytest.fixture
def codex_home(tmp_path):
    (tmp_path / "catalog.json").write_text(json.dumps(CODEX_CATALOG))
    return tmp_path


def collect(codex_home, ctx):
    [result] = CodexLogsSource(codex_home / "codex", codex_home / "catalog.json").collect(ctx)
    return result


def test_codex_logs_ignore_totals_inherited_by_a_forked_session(codex_home, ctx):
    home = codex_home / "codex"
    # a fork starts with the parent's running total: only each event's own usage counts
    write_log(home, "rollout-fork.jsonl", [
        turn("2026-10-04T10:00:00Z", "t1"),
        count("2026-10-04T10:00:05Z", u(250_000_000, 240_000_000, 1_000_000), u(1000, 800, 10)),
        count("2026-10-04T10:00:09Z", u(250_002_000, 240_001_600, 1_000_030), u(2000, 1600, 30)),
        count("2026-10-04T10:00:10Z", u(250_002_000, 240_001_600, 1_000_030), u(2000, 1600, 30)),  # repeated event
    ], tail='{"timestamp": "2026-10-04T10:01:00Z", "type": "event_msg", "payload": {"type": "token_count", "in')  # still being written
    result = collect(codex_home, ctx)
    rows = by_key(result)
    day = rows[("2026-10-04", DAY_TOTAL)]
    assert day.total == 1010 + 2030
    assert (day.input_uncached, day.cache_read, day.output) == (600, 2400, 40)
    assert rows[("2026-10-04", "gpt-6-x")].total == 3040
    assert result.series.source == "codex-logs" and result.series.scope == Scope.MACHINE
    assert result.meta == {"files": 1, "requests": 2, "skipped_lines": 1}


def test_codex_logs_prefer_per_response_records_and_count_each_once(codex_home, ctx):
    home = codex_home / "codex"
    entries = [
        turn("2026-10-04T10:00:00Z", "t1", "gpt-6-x"),
        turn("2026-10-04T10:05:00Z", "t2", "gpt-other"),
        record("2026-10-04T10:00:05Z", "resp_a", "t1", u(1000, 800, 10)),
        count("2026-10-04T10:00:06Z", u(1000, 800, 10), u(1000, 800, 10)),  # same request, ignored
        record("2026-10-04T10:05:05Z", "resp_b", "t2", u(500, 0, 5)),
    ]
    write_log(home, "rollout-a.jsonl", entries)
    write_log(home, "rollout-copy.jsonl", entries[:3])  # history copied into another file
    rows = by_key(collect(codex_home, ctx))
    assert rows[("2026-10-04", DAY_TOTAL)].total == 1010 + 505
    priced = rows[("2026-10-04", "gpt-6-x")]
    assert priced.cost_micros == round((200 * 2 + 800 * 0.2 + 10 * 10)) and priced.cost_quality == CostQuality.LIST_PRICE
    unpriced = rows[("2026-10-04", "gpt-other")]
    assert unpriced.cost_micros is None and unpriced.cost_quality == CostQuality.UNPRICED
    assert rows[("2026-10-04", DAY_TOTAL)].cost_micros == priced.cost_micros


def test_codex_logs_bucket_days_and_price_long_context(codex_home, ctx):
    home = codex_home / "codex"
    write_log(home, "rollout-late.jsonl", [
        turn("2026-10-03T22:00:00Z", "t1"),
        record("2026-10-03T22:30:00Z", "resp_late", "t1", u(1000, 0, 0)),  # 23:30 BST on the 3rd
        record("2026-10-03T23:30:00Z", "resp_long", "t1", u(200_001, 0, 0)),  # 00:30 BST on the 4th
    ])
    rows = by_key(collect(codex_home, ctx))
    assert rows[("2026-10-03", DAY_TOTAL)].total == 1000
    assert rows[("2026-10-04", DAY_TOTAL)].cost_micros == 200_001 * 4


def test_codex_logs_without_logs_or_catalog(codex_home, ctx):
    assert CodexLogsSource(codex_home / "absent", codex_home / "catalog.json").collect(ctx) == []
    write_log(codex_home / "codex", "rollout.jsonl", [])
    with pytest.raises(SourceError, match="catalog"):
        CodexLogsSource(codex_home / "codex", codex_home / "missing.json").collect(ctx)


def test_codex_logs_export_must_match_ledger_timezone(codex_home, ctx, settings):
    home = codex_home / "codex"
    write_log(home, "rollout.jsonl", [turn("2026-10-04T10:00:00Z", "t1"), record("2026-10-04T10:00:01Z", "r", "t1", u(10, 0, 1))])
    raw = collect(codex_home, ctx).raw
    assert parse_logs_export(raw, ctx=ctx).rows  # the archive parses back
    other = SourceContext(settings=type(settings)("UTC", settings.grace_days, settings.machine), now_utc=ctx.now_utc)
    with pytest.raises(SourceError, match="bucketed"):
        parse_logs_export(raw, ctx=other)
