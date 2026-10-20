"""Codex account-wide series: naming the account, folding older names into it, and
reading the Codex app-server."""

from __future__ import annotations

import sys

import pytest

from tokenledger import capture, homes
from tokenledger.config import Paths
from tokenledger.models import Scope, SeriesKey
from tokenledger.sources import SourceContext
from tokenledger.sources.codex_server import LEGACY_ACCOUNT, account_keys, parse_usage_response
from tokenledger.store import repo

from conftest import utc

SIGNED_IN = {
    "result": {
        "account": {"type": "chatgpt", "email": " Me@Example.com ", "planType": "pro"},
        "workspaceRouting": {"chatgptAccountId": "0F0E-ACCOUNT"},
    }
}


@pytest.fixture
def ctx(settings):
    return SourceContext(settings=settings, now_utc=utc("2026-10-20T10:00:00"))


class Once:
    def __init__(self, result):
        self.id, self.result = "codex-server", result

    def collect(self, ctx):
        return [self.result]


def reading(buckets: dict[str, int], keys: dict | None):
    result = parse_usage_response(
        {"result": {"dailyUsageBuckets": [{"startDate": d, "tokens": t} for d, t in buckets.items()]}},
        raw=b"{}", tool_version="t",
    )
    if keys is not None:
        result.meta["account_keys"] = keys
    return result


def run(conn, settings, tmp_path, result):
    return capture.run(conn, Paths(data=tmp_path), settings, usage_sources=[Once(result)], quota_sources=[])


def account_series(conn) -> list[str]:
    return [r["machine"] for r in conn.execute("SELECT machine FROM series WHERE source = 'codex-server' ORDER BY id")]


def test_account_names_are_hashes():
    keys = account_keys(SIGNED_IN)
    assert set(keys) == {"id", "email"}
    assert all(k.startswith("acct-") and "example" not in k and "0f0e" not in k for k in keys.values())
    assert keys == account_keys({"result": {"account": {"type": "chatgpt", "email": "me@example.com"},
                                            "workspaceRouting": {"chatgptAccountId": "0f0e-account"}}})
    assert account_keys({"result": {"account": {"type": "apiKey"}}}) == {}
    assert account_keys({"error": {"message": "offline"}}) == {}


def test_account_id_names_the_series_and_folds_older_names(conn, settings, tmp_path):
    keys = account_keys(SIGNED_IN)
    # A ledger from before accounts were told apart, then a reading with only the email.
    run(conn, settings, tmp_path, reading({"2026-09-01": 10, "2026-09-02": 20}, None))
    assert account_series(conn) == [LEGACY_ACCOUNT]
    run(conn, settings, tmp_path, reading({"2026-09-02": 25}, {"email": keys["email"]}))
    assert account_series(conn) == [keys["email"]]  # the unnamed series was folded in
    # The id arrives: the email's series is folded into it, and the fold is remembered.
    run(conn, settings, tmp_path, reading({"2026-09-03": 5}, keys))
    assert account_series(conn) == [keys["id"]]
    totals = dict(conn.execute("SELECT day, total FROM ledger").fetchall())
    assert totals == {"2026-09-01": 10, "2026-09-02": 20, "2026-09-03": 5}  # final 09-02 kept as it was
    assert repo.account_folds(conn) == [["codex-server", "codex", keys["email"], keys["id"]]]
    # Later readings without an id go to the series the email went to before.
    run(conn, settings, tmp_path, reading({"2026-09-04": 7}, {"email": keys["email"]}))
    assert account_series(conn) == [keys["id"]]


def test_a_reading_that_names_no_account_goes_to_the_last_one(conn, settings, tmp_path):
    keys = account_keys(SIGNED_IN)
    run(conn, settings, tmp_path, reading({"2026-10-18": 10}, keys))
    [outcome] = run(conn, settings, tmp_path, reading({"2026-10-19": 30}, {}))
    assert outcome.ok and account_series(conn) == [keys["id"]]
    # With no earlier reading it is kept under the unnamed series, with a warning.
    conn.execute("DELETE FROM ledger")
    conn.execute("DELETE FROM captures")
    conn.execute("DELETE FROM series")
    run(conn, settings, tmp_path, reading({"2026-10-19": 30}, {}))
    assert account_series(conn) == [LEGACY_ACCOUNT]
    stats = conn.execute("SELECT stats FROM captures WHERE status = 'ok'").fetchone()["stats"]
    assert "named no account" in stats


def test_this_machine_records_the_accounts_it_reads(conn, settings, tmp_path):
    keys = account_keys(SIGNED_IN)
    run(conn, settings, tmp_path, reading({"2026-10-18": 10}, keys))
    row = conn.execute("SELECT accounts FROM machines WHERE id = ?", (settings.machine.id,)).fetchone()
    assert row["accounts"] == f'[["codex", "{keys["id"]}"]]'


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
        result = {
            "account": {"type": "chatgpt", "email": "me@example.com", "planType": "pro"},
            "requiresOpenaiAuth": True,
            "workspaceRouting": {"chatgptAccountId": "0f0e-account"},
        }
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
    assert result.meta["account_keys"] == account_keys(SIGNED_IN)
    assert [(r.day, r.total) for r in result.rows] == [("2026-10-19", 30)]
    assert result.tool_version == "codex-fake 1.0"
    unknown = codex_server.requests(str(binary), [("nope", None)], timeout=10)
    assert unknown[0]["error"]["code"] == -32601


def test_codex_server_is_quiet_where_codex_never_ran(tmp_path, ctx, monkeypatch):
    from tokenledger.sources import codex_server

    monkeypatch.setattr(homes, "codex_homes", lambda **k: [])
    monkeypatch.setenv("PATH", str(tmp_path))
    assert codex_server.CodexServerSource(candidates=(str(tmp_path / "missing"),)).collect(ctx) == []
