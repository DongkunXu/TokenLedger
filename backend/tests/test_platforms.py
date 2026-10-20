"""Sources and helpers that make TokenLedger work on Windows and Linux: Claude Code logs,
the models.dev catalog, Codex compressed logs, data folders."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import zstandard

from tokenledger import catalog, homes
from tokenledger.models import DAY_TOTAL, CostQuality, Scope, SeriesKey
from tokenledger.pricing import Catalog, claude_cost_usd, normalize_claude_model
from tokenledger.sources import SourceContext
from tokenledger.sources.claude_logs import MAX_LINE_BYTES, ClaudeLogsSource, parse_logs_export, parse_timestamp
from tokenledger.sources.codex_logs import CodexLogsSource

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


def raw_entry(ts, mid, rid, usage, **extra):
    return json.dumps({"type": "assistant", "timestamp": ts, "requestId": rid, "sessionId": "s1",
                       "message": {"id": mid, "model": "claude-opus-5-5", "usage": usage}, **extra},
                      separators=(",", ":"))


def test_lines_are_read_like_codexbar(tmp_path, cat, ctx):
    """CostUsageClaudeMetadataParsingTests, CostUsageScannerTests: the exact ``"type":"assistant"``
    marker, JSONSerialization's leniency and strictness, the 512 KiB limit and scalar coercion."""
    usage = {"input_tokens": 1, "output_tokens": 1}
    u = json.dumps(usage, separators=(",", ":"))
    lines = [
        raw_entry("2026-10-19T10:00:00Z", "m1", "r1", usage),
        raw_entry("2026-10-19T10:00:01Z", "m2", "r2", usage).replace('"type":"assistant"', '"type": "assistant"'),
        raw_entry("2026-10-19T10:00:02Z", "m3", "r3", usage).replace(u, u[:-1] + ",}"),  # trailing comma: accepted
        raw_entry("2026-10-19T10:00:03Z", "m4", "r4", usage, note="\ud800"),  # unpaired surrogate: line rejected
        raw_entry("2026-10-19T10:00:04Z", "m5", "r5", usage).replace(u, u[:-1] + ',"x":NaN}'),
        raw_entry("2026-10-19T10:00:05Z", "m6", "r6", usage)[:-1] + ',"type":"user"}',  # the first of two keys wins
        raw_entry("2026-10-19T10:00:06Z", "m7", "r7", usage, note="\U0001F600"),  # a surrogate pair is fine
        raw_entry("2026-10-19T10:00:07Z", "m8", "r8", {"input_tokens": True, "output_tokens": 1.75,
                                                        "cache_read_input_tokens": "2", "cache_creation_input_tokens": -1}),
    ]
    root = tmp_path / "projects"
    (root / "p").mkdir(parents=True)
    (root / "p" / "s.jsonl").write_text("\n".join(lines) + "\n")
    base = raw_entry("2026-10-19T11:00:00Z", "big", "big", usage, pad="")
    exact = base.replace('"pad":""', '"pad":"' + "a" * (MAX_LINE_BYTES - len(base)) + '"')
    over = exact.replace('"big"', '"bog"')
    (root / "p" / "large.jsonl").write_bytes(exact.encode() + b"\n" + over.encode() + b"\r\n")  # \r counts
    rows, result = collect([root], cat, ctx)
    assert rows[("2026-10-19", DAY_TOTAL)].total == 2 * 5 + 2  # m1, m3, m6, m7, big; m8 is 1 + 1
    assert result.meta["skipped_lines"] == 3  # m4, m5 and the oversized line


@pytest.mark.parametrize("stamp, day", [
    ("2026-10-19T23:59:59.999999999+05:30", "2026-10-19"),
    ("2026-10-19T23:30:00+0530", "2026-10-19"),  # compact offsets are read too
    ("2026-10-19T21:30:00-01", "2026-10-19"),
    ("2026-10-19T23:30:00.5Z", "2026-10-20"),  # 00:30 BST
    ("2026-02-30T12:00:00Z", None),
    ("1899-12-31T12:00:00Z", None),
    ("2026-10-19T24:00:00Z", None),
    ("2016-12-31T23:59:60Z", None),
    ("2026-10-19T12:00:00.Z", None),
    ("2026-10-19T12:00:00.1234567890Z", None),
    ("2026-10-19t12:00:00z", None),
    ("2026-10-19T12:00:00+25:00", None),
    ("2026-10-19T12:00:00", None),
    ("2026-10-19", None),
])
def test_timestamps(tmp_path, cat, ctx, stamp, day):
    root = tmp_path / "projects"
    write(root / "p" / "s.jsonl", [entry(stamp, "m1", "r1")])
    rows, _ = collect([root], cat, ctx)
    assert [d for d, m in rows if m == DAY_TOTAL] == ([day] if day else [])
    if day is None:
        return
    at = parse_timestamp(stamp)
    assert at.microsecond % 1000 == 0  # milliseconds, like CodexBar


def test_streaming_placeholders_and_unkeyed_entries(tmp_path, cat, ctx):
    """CostUsageClaudePriceRangeTests, CostUsageScannerClaudeProxyIdentityTests."""
    def chunk(mid, out, *, incomplete=False, inp=10, rid="r", session="s1"):
        usage = {"input_tokens": inp, "output_tokens": out}
        if not incomplete:
            usage |= {"cache_read_input_tokens": 3, "cache_creation_input_tokens": 4}
        message = {"id": mid, "model": "claude-opus-5-5", "usage": usage, "stop_reason": None if incomplete else "end_turn"}
        e = {"type": "assistant", "timestamp": "2026-10-19T10:00:00Z", "sessionId": session, "message": message}
        return e | ({"requestId": rid} if rid else {})

    root = tmp_path / "projects"
    write(root / "p" / "s.jsonl", [
        chunk("a", 0, incomplete=True),
        chunk("a", 4),
        chunk("a", 0, incomplete=True, inp=999),  # a later placeholder does not replace the complete chunk
        chunk("b", 0, incomplete=True, rid=None),  # only a placeholder: counted, no tokens
        chunk("c", 3, rid=None),
        chunk("c", 3, rid=None),  # session + message id: once
        chunk(" ", 1, rid=None),
        chunk(" ", 1, rid=None),  # a blank message id is no identity: both count
        chunk("d", 1, rid=None, session=" \n\t"),
        chunk("d", 1, rid=None, session=" \n\t"),
    ])
    rows, _ = collect([root], cat, ctx)
    day = rows[("2026-10-19", DAY_TOTAL)]
    assert (day.input_uncached, day.output) == (10 + 10 + 2 * 10 + 2 * 10, 4 + 3 + 2 + 2)


def test_cross_file_copies(tmp_path, cat, ctx):
    """CostUsageScannerClaudeRegressionTests: the main session beats subagent copies, then the
    first path; the request id alone identifies a request."""
    root = tmp_path / "projects"
    write(root / "p" / "s" / "subagents" / "agent-a.jsonl", [entry("2026-10-19T10:00:00Z", "m1", "r1", out=1, sidechain=True)])
    write(root / "p" / "s" / "subagents" / "agent-b.jsonl", [entry("2026-10-19T10:00:01Z", "m1", "r1", out=999, sidechain=True)])
    write(root / "p" / "x.jsonl", [entry("2026-10-19T10:00:00Z", "m2", "r2", out=10, sessionId=None)])
    write(root / "p" / "y" / "subagents" / "agent.jsonl", [entry("2026-10-19T10:00:01Z", "m2", "r2", out=20, sidechain=True)])
    rows, _ = collect([root], cat, ctx)
    assert rows[("2026-10-19", DAY_TOTAL)].output == 1 + 10


# -- folders ------------------------------------------------------------------------------


def test_claude_log_roots(tmp_path, monkeypatch):
    """ClaudeConfigPaths.costProjectsRoots, CostUsageScannerClaudeSwapTests/DesktopTests."""
    home = tmp_path / "home"
    for p in (".claude/projects", ".config/claude/projects", "custom/projects"):
        (home / p).mkdir(parents=True)
    sessions = home / ".claude-swap-backup" / "sessions"
    for slot in ("1-first", "2-second", "01-padded", "1-", "0-zero", "x-label", ".9-hidden", "unrelated"):
        (sessions / slot / "projects").mkdir(parents=True)
    (sessions / "3-shared").mkdir()
    (sessions / "3-shared" / "projects").symlink_to(sessions / "1-first" / "projects")
    (sessions / "4-missing").mkdir()
    (sessions / "4-missing" / "projects").symlink_to(tmp_path / "gone")
    app = home / "app"
    agent = app / "local-agent-mode-sessions" / "workspace" / "session" / "local_agent"
    (agent / ".claude" / "projects").mkdir(parents=True)
    (agent / "audit.jsonl").write_text("")
    (agent / "outputs" / "x").mkdir(parents=True)
    (agent / "outputs" / "x" / "audit.jsonl").write_text("")  # build output folders are not entered
    (app / "claude-code-sessions" / "account" / "org" / ".claude" / "projects").mkdir(parents=True)
    monkeypatch.setattr(homes, "desktop_app_dirs", lambda **k: [app])

    swap = [sessions / s / "projects" for s in ("01-padded", "1-", "1-first", "2-second")]
    desktop = [agent / ".claude" / "projects", agent / "audit.jsonl",
               app / "claude-code-sessions" / "account" / "org" / ".claude" / "projects"]
    assert homes.claude_log_roots(home=home, env={}) == [
        home / ".config/claude/projects", home / ".claude/projects", *swap, *desktop]
    # CLAUDE_CONFIG_DIR is the one config folder, and the desktop app is not read
    assert homes.claude_log_roots(home=home, env={"CLAUDE_CONFIG_DIR": str(home / "custom")}) == [
        home / "custom/projects", *swap]


def test_desktop_audit_log_shares_request_identity(tmp_path, cat, ctx):
    """CostUsageScannerClaudeDesktopTests: Cowork's audit.jsonl and the mirrored session count once."""
    def assistant(session, output):
        return {"type": "assistant", "timestamp": "2026-10-19T10:00:00Z", "session_id": session,
                "message": {"id": "response-a", "model": "claude-opus-5-5", "usage": {"input_tokens": 10, "output_tokens": output}}}

    audit = tmp_path / "agent" / "audit.jsonl"
    write(audit, [assistant("cowork-a", 1), assistant("cowork-a", 5), assistant("cowork-b", 7),
                  {"type": "result", "timestamp": "2026-10-19T10:00:00Z", "usage": {"input_tokens": 999, "output_tokens": 999}}])
    write(tmp_path / "projects" / "mirror" / "session.jsonl", [assistant("cowork-a", 5)])
    rows, _ = collect([audit, tmp_path / "projects"], cat, ctx)
    day = rows[("2026-10-19", DAY_TOTAL)]
    assert (day.input_uncached, day.output) == (20, 12)


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


def test_catalog_falls_back_to_the_last_copy_and_backs_off(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog, "codexbar_paths", lambda: [])
    monkeypatch.setenv("TOKENLEDGER_DATA_DIR", str(tmp_path))
    attempts = []

    def offline(path, current, *, now):
        attempts.append(now)
        raise catalog.CatalogError("offline")

    monkeypatch.setattr(catalog, "refresh", offline)
    now = datetime(2026, 10, 20, 8, 0, tzinfo=timezone.utc)
    with pytest.raises(catalog.CatalogError):
        catalog.catalog_bytes(fetch=False)
    for _ in range(2):  # no copy at all: every capture tries
        with pytest.raises(catalog.CatalogError):
            catalog.catalog_bytes(now=now)
    assert len(attempts) == 2

    stale = json.dumps({"version": 1, "fetchedAt": "2020-01-01T00:00:00Z", "catalog": {"providers": {}}}).encode()
    catalog.own_path().parent.mkdir(parents=True)
    catalog.own_path().write_bytes(stale)
    assert catalog.catalog_bytes(now=now) == stale  # refresh failed: the old copy is used
    assert catalog.catalog_bytes(now=now + timedelta(hours=5)) == stale  # and the next try waits
    assert len(attempts) == 3
    untrusted = []
    monkeypatch.setattr(catalog, "refresh", lambda path, current, *, now: untrusted.append(now))  # returns None
    assert catalog.catalog_bytes(now=now + catalog.RETRY_AFTER) == stale
    assert catalog.catalog_bytes(now=now + catalog.RETRY_AFTER + timedelta(hours=1)) == stale
    assert len(untrusted) == 1  # an untrusted fetch backs off too

    fresh = json.dumps({"version": 1, "fetchedAt": "2026-10-21T00:00:00Z", "catalog": {"providers": {}}}).encode()
    monkeypatch.setattr(catalog, "refresh", lambda path, current, *, now: fresh)
    assert catalog.catalog_bytes(now=now + 2 * catalog.RETRY_AFTER) == fresh
    assert not catalog.failed_path().exists()
