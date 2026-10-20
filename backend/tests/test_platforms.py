"""Sources and helpers that make TokenLedger work on Windows and Linux: Claude Code logs,
the models.dev catalog, Codex compressed logs and account keys, data folders."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import zstandard

from tokenledger import catalog, homes
from tokenledger.models import DAY_TOTAL, CostQuality, Scope, SeriesKey
from tokenledger.pricing import Catalog, claude_cost_usd, normalize_claude_model
from tokenledger.sources import SourceContext, SourceError
from tokenledger.sources.claude_logs import ClaudeLogsSource, parse_logs_export
from tokenledger.sources.codex_logs import CodexLogsSource
from tokenledger.sources.codex_server import account_key, parse_usage_response
from tokenledger.store import repo

from conftest import utc

ANTHROPIC = {
    "anthropic": {
        "id": "anthropic",
        "models": {
            "claude-opus-5-5": {"id": "claude-opus-5-5", "cost": {"input": 4, "output": 20, "cache_read": 0.4, "cache_write": 5}},
            "claude-long": {
                "id": "claude-long",
                "cost": {"input": 3, "output": 15, "context_over_200k": {"input": 6, "output": 22.5}},
            },
        },
    }
}


@pytest.fixture
def ctx(settings):
    return SourceContext(settings=settings, now_utc=utc("2026-10-20T10:00:00"))


@pytest.fixture
def cat():
    return Catalog(providers=ANTHROPIC, fetched_at="2026-10-20T00:00:00Z")


def entry(ts, mid, rid, *, model="claude-opus-5-5", inp=2, cw=100, cw1h=100, cr=1000, out=50, sidechain=False, **extra):
    usage = {
        "input_tokens": inp,
        "cache_creation_input_tokens": cw,
        "cache_read_input_tokens": cr,
        "output_tokens": out,
        "cache_creation": {"ephemeral_1h_input_tokens": cw1h, "ephemeral_5m_input_tokens": cw - cw1h},
    }
    message = {"id": mid, "model": model, "usage": usage, "stop_reason": "tool_use", "content": [{"type": "text"}]}
    return {"type": "assistant", "timestamp": ts, "requestId": rid, "sessionId": "s1", "isSidechain": sidechain,
            "message": message, **extra}


def write(path: Path, entries, tail=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(e, separators=(",", ":")) + "\n" for e in entries) + tail)


def collect(roots, cat, ctx):
    [result] = ClaudeLogsSource(roots=roots, catalog=cat).collect(ctx)
    return {(r.day, r.model): r for r in result.rows}, result


# -- claude-logs --------------------------------------------------------------------------


def test_each_request_counts_once_across_blocks_files_and_folders(tmp_path, cat, ctx):
    a = entry("2026-10-19T10:00:00Z", "msg_1", "req_1")
    a2 = {**a, "message": {**a["message"], "content": [{"type": "tool_use"}]}}  # second block, same usage
    b = entry("2026-10-19T11:00:00.123Z", "msg_2", "req_2", out=10)
    root1, root2 = tmp_path / "one" / "projects", tmp_path / "two" / "projects"
    write(root1 / "p" / "s1.jsonl", [a, a2, b], tail='{"type":"assistant","usage"')  # last line still being written
    write(root2 / "p" / "copy.jsonl", [a])  # a session copied to another folder (teleport, sync tool)
    write(root1 / "p" / "s1" / "subagents" / "agent-1.jsonl",
          [entry("2026-10-19T12:00:00Z", "msg_3", "req_3", sidechain=True, cw=0, cw1h=0)])
    rows, result = collect([root1, root2], cat, ctx)
    day = rows[("2026-10-19", DAY_TOTAL)]
    assert day.total == (2 + 100 + 1000 + 50) + (2 + 100 + 1000 + 10) + (2 + 0 + 1000 + 50)
    assert (day.input_uncached, day.cache_write, day.cache_read, day.output) == (6, 200, 3000, 110)
    assert result.series == SeriesKey("claude-logs", "claude", "test-mac", Scope.MACHINE)
    assert result.meta["requests"] == 3 and result.meta["skipped_lines"] == 1


def test_skips_placeholders_errors_vertex_and_zoneless_times(tmp_path, cat, ctx):
    root = tmp_path / "projects"
    write(root / "p" / "s.jsonl", [
        entry("2026-10-19T10:00:00Z", "msg_1", "req_1"),
        entry("2026-10-19T10:00:01Z", "msg_s", "req_s", model="<synthetic>", inp=0, cw=0, cw1h=0, cr=0, out=0),
        entry("2026-10-19T10:00:02Z", "msg_vrtx_1", "req_v"),
        entry("2026-10-19T10:00:03", "msg_4", "req_4"),  # no timezone
        entry("2026-10-19T10:00:04Z", "msg_5", "req_5", metadata={"provider": "vertex-ai"}),
    ])
    rows, _ = collect([root], cat, ctx)
    assert rows[("2026-10-19", DAY_TOTAL)].total == 2 + 100 + 1000 + 50


def test_last_entry_in_a_file_wins_and_main_session_beats_subagent_copy(tmp_path, cat, ctx):
    root = tmp_path / "projects"
    first = entry("2026-10-19T10:00:00Z", "msg_1", "req_1", out=4)
    last = entry("2026-10-19T10:00:09Z", "msg_1", "req_1", out=400)
    write(root / "p" / "s.jsonl", [first, last])
    write(root / "a" / "s" / "subagents" / "agent.jsonl", [entry("2026-10-19T10:00:09Z", "msg_1", "req_1", out=999)])
    rows, _ = collect([root], cat, ctx)
    assert rows[("2026-10-19", DAY_TOTAL)].output == 400


def test_days_follow_the_ledger_timezone_and_models_are_normalized(tmp_path, cat, ctx):
    root = tmp_path / "projects"
    write(root / "p" / "s.jsonl", [
        entry("2026-10-19T23:30:00Z", "msg_1", "req_1", model="claude-haiku-4-5-20251001"),  # 00:30 BST on the 20th
    ])
    rows, _ = collect([root], cat, ctx)
    assert ("2026-10-20", "claude-haiku-4-5") in rows


def test_cost_rules(cat):
    at = datetime(2026, 10, 19, tzinfo=timezone.utc)
    # models.dev rates; 1-hour writes cost twice the input rate
    usd = claude_cost_usd(cat, "claude-opus-5-5", input_tokens=10, cache_read=1000, cache_write=300, cache_write_1h=100,
                          output=50, at=at)
    assert usd == pytest.approx((10 * 4 + 1000 * 0.4 + 200 * 5 + 100 * 4 * 2 + 50 * 20) / 1e6)
    # missing cache rates fall back to input; above 200K the whole request takes the higher tier
    long = claude_cost_usd(cat, "claude-long", input_tokens=150_000, cache_read=50_001, cache_write=0, cache_write_1h=0,
                           output=10, at=at)
    assert long == pytest.approx((150_000 * 6 + 50_001 * 3 + 10 * 22.5) / 1e6)
    # built-in table when models.dev does not list the model; dated rates for opus/sonnet 4.6
    assert claude_cost_usd(cat, "claude-opus-4-1", input_tokens=1, cache_read=0, cache_write=0, cache_write_1h=0,
                           output=0, at=at) == pytest.approx(15e-6)
    before = datetime(2026, 3, 1, tzinfo=timezone.utc)
    assert claude_cost_usd(cat, "claude-sonnet-4-6", input_tokens=200_001, cache_read=0, cache_write=0, cache_write_1h=0,
                           output=0, at=before) == pytest.approx(200_001 * 6e-6)
    assert claude_cost_usd(cat, "claude-unknown", input_tokens=1, cache_read=0, cache_write=0, cache_write_1h=0,
                           output=0, at=at) is None
    assert normalize_claude_model("us.anthropic.claude-sonnet-4-5-20250929-v1:0") == "claude-sonnet-4-5"
    assert normalize_claude_model("claude-sonnet-4-20250514") == "claude-sonnet-4-20250514"


def test_unpriced_model_day_stays_out_of_the_cost(tmp_path, cat, ctx):
    root = tmp_path / "projects"
    write(root / "p" / "s.jsonl", [
        entry("2026-10-19T10:00:00Z", "msg_1", "req_1"),
        entry("2026-10-19T10:00:01Z", "msg_2", "req_2", model="claude-mystery"),
    ])
    rows, result = collect([root], cat, ctx)
    assert rows[("2026-10-19", "claude-mystery")].cost_quality == CostQuality.UNPRICED
    priced = rows[("2026-10-19", "claude-opus-5-5")]
    assert rows[("2026-10-19", DAY_TOTAL)].cost_micros == priced.cost_micros
    assert parse_logs_export(result.raw, ctx=ctx).rows == result.rows  # the archive parses back


def test_no_logs_means_nothing_to_report(tmp_path, cat, ctx):
    assert ClaudeLogsSource(roots=[], catalog=cat).collect(ctx) == []


# -- folders ------------------------------------------------------------------------------


def test_claude_log_roots(tmp_path):
    home = tmp_path / "home"
    for p in (".claude/projects", ".config/claude/projects", "custom/projects", ".claude-swap-backup/sessions/2-work/projects"):
        (home / p).mkdir(parents=True)
    env = {"CLAUDE_CONFIG_DIR": str(home / "custom")}
    roots = homes.claude_log_roots(home=home, env=env)
    assert roots[:3] == [home / "custom/projects", home / ".config/claude/projects", home / ".claude/projects"]
    assert home / ".claude-swap-backup/sessions/2-work/projects" in roots


def test_codex_homes(tmp_path):
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (tmp_path / "elsewhere").mkdir()
    assert homes.codex_homes(home=home, env={"CODEX_HOME": str(tmp_path / "elsewhere")}) == [tmp_path / "elsewhere", home / ".codex"]
    assert homes.codex_homes(home=home, env={"CODEX_HOME": str(home / ".codex")}) == [home / ".codex"]  # once


def test_wsl_list_output_is_utf16():
    raw = "Ubuntu\r\ndocker-desktop\r\nDebian\r\n".encode("utf-16-le")
    assert homes.parse_wsl_list(raw) == ["Ubuntu", "Debian"]
    assert homes.parse_wsl_list(b"Ubuntu\nDebian\n") == ["Ubuntu", "Debian"]
    if sys.platform != "win32":
        assert homes.wsl_homes("all") == []


# -- Codex --------------------------------------------------------------------------------


def test_codex_reads_compressed_logs(tmp_path, ctx):
    from test_sources import CODEX_CATALOG, record, turn, u

    (tmp_path / "catalog.json").write_text(json.dumps(CODEX_CATALOG))
    day = tmp_path / "codex" / "sessions" / "2026" / "10" / "04"
    day.mkdir(parents=True)
    lines = "".join(json.dumps(e) + "\n" for e in [turn("2026-10-04T10:00:00Z", "t1"), record("2026-10-04T10:00:01Z", "r1", "t1", u(100, 0, 10))])
    (day / "rollout-old.jsonl.zst").write_bytes(zstandard.ZstdCompressor(level=3).compress(lines.encode()))
    (day / "rollout-broken.jsonl.zst").write_bytes(b"\x28\xb5\x2f\xfd garbage")
    [result] = CodexLogsSource(tmp_path / "codex", tmp_path / "catalog.json").collect(ctx)
    assert {(r.day, r.model): r.total for r in result.rows}[("2026-10-04", DAY_TOTAL)] == 110


def test_codex_account_key():
    signed_in = {"result": {"account": {"type": "chatgpt", "email": " Me@Example.com ", "planType": "pro"}}}
    key = account_key(signed_in)
    assert key.startswith("acct-") and "example" not in key
    assert key == account_key({"result": {"account": {"type": "chatgpt", "email": "me@example.com"}}})
    assert key != account_key({"result": {"account": {"type": "chatgpt", "email": "other@example.com"}}})
    with pytest.raises(SourceError, match="ChatGPT account"):
        account_key({"result": {"account": {"type": "apiKey"}}})
    with pytest.raises(SourceError, match="account/read failed"):
        account_key({"error": {"message": "offline"}})


def test_legacy_account_series_is_folded_into_the_known_account(conn, settings, tmp_path):
    from tokenledger import capture
    from tokenledger.config import Paths

    legacy = parse_usage_response(
        {"result": {"dailyUsageBuckets": [{"startDate": "2026-09-01", "tokens": 10}, {"startDate": "2026-09-02", "tokens": 20}]}},
        raw=b"{}", tool_version="t",
    )
    assert legacy.series.machine == "account"

    class Once:
        def __init__(self, result):
            self.id, self.result = "codex-server", result

        def collect(self, ctx):
            return [self.result]

    paths = Paths(data=tmp_path)
    capture.run(conn, paths, settings, usage_sources=[Once(legacy)], quota_sources=[])
    known = parse_usage_response(
        {"result": {"dailyUsageBuckets": [{"startDate": "2026-09-02", "tokens": 25}, {"startDate": "2026-09-03", "tokens": 5}]}},
        raw=b"{}", tool_version="t", account="acct-1",
    )
    capture.run(conn, paths, settings, usage_sources=[Once(known)], quota_sources=[])
    series = conn.execute("SELECT machine FROM series WHERE source = 'codex-server'").fetchall()
    assert [s["machine"] for s in series] == ["acct-1"]
    totals = dict(conn.execute("SELECT day, total FROM ledger").fetchall())
    assert totals == {"2026-09-01": 10, "2026-09-02": 20, "2026-09-03": 5}  # final 09-02 kept as it was
    assert repo.find_series(conn, SeriesKey("codex-server", "codex", "account", Scope.ACCOUNT)) is None


# -- catalog ------------------------------------------------------------------------------


def test_catalog_is_built_like_codexbar(tmp_path, monkeypatch):
    api = {
        "anthropic": {"id": "anthropic", "name": "Anthropic", "npm": "x", "models": {
            "claude-a": {"id": "claude-a", "name": "A", "cost": {"input": 1, "output": 2, "reasoning": 9}, "limit": {"context": 5, "output": 1}},
            "bad": {"id": 7},
        }},
        "openai": {"id": "openai", "models": {"gpt-x": {"id": "gpt-x", "cost": {"input": 1, "output": 2}}}},
    }
    now = datetime(2026, 10, 20, 8, 15, 30, 123456, tzinfo=timezone.utc)
    built = catalog.build(api, None, now)
    assert built["fetchedAt"] == "2026-10-20T08:15:30Z"
    assert built["catalog"]["providers"]["anthropic"] == {
        "id": "anthropic", "name": "Anthropic",
        "models": {"claude-a": {"id": "claude-a", "name": "A", "cost": {"input": 1, "output": 2}, "limit": {"context": 5}}},
    }
    # a model models.dev dropped stays, so old days keep their price
    old = {"catalog": {"providers": {"anthropic": {"id": "anthropic", "models": {
        "claude-old": {"id": "claude-old", "cost": {"input": 3, "output": 4}}}}}}}
    assert "claude-old" in catalog.build(api, old, now)["catalog"]["providers"]["anthropic"]["models"]
    # a fetch without priced Anthropic and OpenAI models is not trusted
    assert catalog.build({"openai": api["openai"]}, None, now) is None


def test_catalog_falls_back_to_the_last_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog, "codexbar_paths", lambda: [])
    monkeypatch.setenv("TOKENLEDGER_DATA_DIR", str(tmp_path))
    with pytest.raises(catalog.CatalogError):
        catalog.catalog_bytes(fetch=False)
    stale = json.dumps({"version": 1, "fetchedAt": "2020-01-01T00:00:00Z", "catalog": {"providers": {}}}).encode()
    catalog.own_path().parent.mkdir(parents=True)
    catalog.own_path().write_bytes(stale)

    def offline(*a, **k):
        raise catalog.CatalogError("offline")

    monkeypatch.setattr(catalog, "refresh", offline)
    assert catalog.catalog_bytes() == stale  # refresh failed: the old copy is used


# -- codex app-server over stdio (a reader thread, as on Windows) --------------------------

FAKE_SERVER = r'''
import json, sys
for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg:
        continue  # the initialized notification
    method = msg["method"]
    if method == "initialize":
        result = {"userAgent": "fake"}
    elif method == "account/read":
        result = {"account": {"type": "chatgpt", "email": "me@example.com", "planType": "pro"}, "requiresOpenaiAuth": True}
    elif method == "account/usage/read":
        result = {"summary": {"lifetimeTokens": 30}, "dailyUsageBuckets": [{"startDate": "2026-10-19", "tokens": 30}]}
    else:
        print(json.dumps({"id": msg["id"], "error": {"code": -32601, "message": "unknown"}}), flush=True)
        continue
    print("not json, a log line", flush=True)
    print(json.dumps({"id": msg["id"], "result": result}), flush=True)
'''


def test_codex_server_round_trip(tmp_path, ctx, monkeypatch):
    from tokenledger.sources import codex_server

    script = tmp_path / "fake_codex.py"
    script.write_text(FAKE_SERVER)
    if sys.platform == "win32":
        pytest.skip("the fake server is a POSIX script")
    binary = tmp_path / "codex"
    binary.write_text(f"#!/bin/sh\nif [ \"$1\" = --version ]; then echo codex-fake 1.0; exit 0; fi\nexec {sys.executable} {script}\n")
    binary.chmod(0o755)
    [result] = codex_server.CodexServerSource(candidates=(str(binary),)).collect(ctx)
    assert result.series.machine == account_key({"result": {"account": {"type": "chatgpt", "email": "me@example.com"}}})
    assert [(r.day, r.total) for r in result.rows] == [("2026-10-19", 30)]
    assert result.tool_version == "codex-fake 1.0"
    unknown = codex_server.requests(str(binary), [("nope", None)], timeout=10)
    assert unknown[0]["error"]["code"] == -32601


def test_codex_server_is_quiet_where_codex_never_ran(tmp_path, ctx, monkeypatch):
    from tokenledger.sources import codex_server

    monkeypatch.setattr(homes, "codex_homes", lambda **k: [])
    monkeypatch.setenv("PATH", str(tmp_path))
    assert codex_server.CodexServerSource(candidates=(str(tmp_path / "missing"),)).collect(ctx) == []
