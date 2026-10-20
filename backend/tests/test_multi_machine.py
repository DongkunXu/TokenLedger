"""What a hub's views show once several machines have pushed into its ledger."""

from __future__ import annotations

from tokenledger import views
from tokenledger.config import Machine
from tokenledger.models import Scope, SeriesKey
from tokenledger.store import repo

from test_sync import capture_into, make_settings, usage

HUB = Machine("hub", "Hub", "darwin")
PC = Machine("pc", "PC", "windows")


def test_machine_sources_add_up_and_accounts_add_up(conn):
    hub = make_settings(HUB)
    pc = make_settings(PC)
    # Claude on the hub through CodexBar and on the PC through its logs: added up.
    capture_into(conn, hub, SeriesKey("codexbar", "claude", "hub", Scope.MACHINE), [usage("2026-10-10", 100, cost=1)], "2026-10-19T10:00:00")
    capture_into(conn, pc, SeriesKey("claude-logs", "claude", "pc", Scope.MACHINE), [usage("2026-10-10", 40, cost=2)], "2026-10-19T10:00:00")
    # On the hub, claude-logs also read the same Mac: CodexBar is preferred for that machine.
    capture_into(conn, hub, SeriesKey("claude-logs", "claude", "hub", Scope.MACHINE), [usage("2026-10-10", 99, cost=1)], "2026-10-19T10:00:00")
    # Two Codex accounts, each read on one machine: added up.
    capture_into(conn, hub, SeriesKey("codex-server", "codex", "acct-a", Scope.ACCOUNT), [usage("2026-10-10", 500)], "2026-10-19T10:00:00")
    capture_into(conn, pc, SeriesKey("codex-server", "codex", "acct-b", Scope.ACCOUNT), [usage("2026-10-10", 300)], "2026-10-19T10:00:00")
    daily = views.daily(conn, "2026-10-10", "2026-10-10")
    got = {p["provider"]: p["values"][0] for p in daily["providers"]}
    assert got["claude"]["tokens"] == 140 and got["claude"]["cost_usd"] == 3e-6
    assert got["codex"]["tokens"] == 800 and got["codex"]["basis"] == "account"


def test_source_health_per_machine(conn):
    hub = make_settings(HUB)
    with repo.transaction(conn):
        repo.ensure_machine(conn, PC, "2026-10-19T00:00:00+00:00")
        for machine, source, at, status in [
            (None, "codexbar", "2026-10-19T10:00:00+00:00", "ok"),
            ("pc", "claude-logs", "2026-10-19T09:00:00+00:00", "error"),
            ("pc", "codexbar", "2026-10-10T09:00:00+00:00", "error"),  # no longer run on the PC
        ]:
            cid = repo.start_capture(conn, "usage", source, None, at)
            repo.finish_capture(conn, cid, at, status=status, error=None if status == "ok" else "boom")
            conn.execute("UPDATE captures SET machine = ? WHERE id = ?", (machine, cid))
    health = views.source_health(conn, hub.machine.id)
    assert [(h["machine"], h["machine_label"], h["source"], h["ok"]) for h in health] == [
        ("hub", "hub", "codexbar", True),
        ("pc", "PC", "claude-logs", False),
    ]
    assert [(m["id"], m["this_machine"]) for m in views.machines(conn, hub.machine.id)] == [("pc", False)]
