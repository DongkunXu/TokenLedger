from __future__ import annotations

import gzip
from itertools import count

from tokenledger import capture
from tokenledger.config import Paths
from tokenledger.models import QuotaCapture
from tokenledger.sources import SourceError
from tokenledger.sources.codexbar import parse_cost_export
from tokenledger.sources.codexbar_quota import parse_history_file

from conftest import FIXTURES, utc


class FakeUsage:
    id = "codexbar"

    def collect(self, ctx):
        raw = (FIXTURES / "codexbar-claude.json").read_bytes()
        return [parse_cost_export(raw, provider="claude", ctx=ctx, tool_version="CodexBar test")]


class Broken:
    id = "broken"

    def collect(self, ctx):
        raise SourceError("tool missing")


class FakeQuota:
    id = "codexbar-quota"

    def collect(self, ctx):
        samples = parse_history_file("claude", (FIXTURES / "history-claude.json").read_bytes())
        return QuotaCapture(source=self.id, samples=samples, tool_version="t", raw_name="", raw=b"")


def clock():
    seconds = count()
    return lambda: utc(f"2026-09-27T10:00:{next(seconds):02d}")


def test_run_records_success_failure_and_raw(conn, settings, tmp_path):
    paths = Paths(data=tmp_path)
    outcomes = capture.run(
        conn, paths, settings, usage_sources=[FakeUsage(), Broken()], quota_sources=[FakeQuota()], clock=clock()
    )
    assert [o.ok for o in outcomes] == [True, False, True]
    assert outcomes[1].error == "tool missing"
    caps = conn.execute("SELECT kind, source, status, raw_path FROM captures ORDER BY id").fetchall()
    assert [(c["kind"], c["source"], c["status"]) for c in caps] == [
        ("usage", "codexbar", "ok"),
        ("usage", "broken", "error"),
        ("quota", "codexbar-quota", "ok"),
    ]
    raw = tmp_path / caps[0]["raw_path"]
    assert gzip.decompress(raw.read_bytes()) == (FIXTURES / "codexbar-claude.json").read_bytes()
    assert conn.execute("SELECT COUNT(*) FROM ledger").fetchone()[0] == 5
    assert conn.execute("SELECT COUNT(*) FROM quota_samples").fetchone()[0] == 3


def test_quota_samples_are_not_duplicated(conn, settings, tmp_path):
    paths = Paths(data=tmp_path)
    for _ in range(2):
        capture.run(conn, paths, settings, usage_sources=[], quota_sources=[FakeQuota()], clock=clock())
    assert conn.execute("SELECT COUNT(*) FROM quota_samples").fetchone()[0] == 3


def test_accept_revision_rebuilds_row_from_archive(conn, settings, tmp_path):
    import json

    from tokenledger.corrections import accept_revision

    paths = Paths(data=tmp_path)
    capture.run(conn, paths, settings, usage_sources=[FakeUsage()], quota_sources=[], clock=clock())

    class Changed(FakeUsage):
        def collect(self, ctx):
            payload = json.loads((FIXTURES / "codexbar-claude.json").read_bytes())
            day = payload[0]["daily"][0]  # 2026-08-01, already final
            day["cacheReadTokens"] += 100
            day["totalTokens"] += 100
            raw = json.dumps(payload).encode()
            return [parse_cost_export(raw, provider="claude", ctx=ctx, tool_version="t")]

    capture.run(conn, paths, settings, usage_sources=[Changed()], quota_sources=[], clock=clock())
    rev = conn.execute("SELECT id, kind, observed_total, ledger_total FROM revisions WHERE model='*'").fetchone()
    assert (rev["kind"], rev["observed_total"], rev["ledger_total"]) == ("changed", 1450, 1350)
    result = accept_revision(conn, paths, settings, rev["id"], now_utc=utc("2026-09-27T12:00:00"))
    assert result["new_total"] == 1450
    row = conn.execute("SELECT total, cache_read, origin FROM ledger WHERE day='2026-08-01' AND model='*'").fetchone()
    assert (row["total"], row["cache_read"], row["origin"]) == (1450, 1100, f"manual:revision:{rev['id']}")
