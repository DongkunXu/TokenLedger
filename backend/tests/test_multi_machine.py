"""What the views show once several machines share one ledger."""

from __future__ import annotations

from tokenledger import views
from tokenledger.config import Machine
from tokenledger.models import Scope, SeriesKey
from tokenledger.store import repo

from test_sync import capture_into, make_settings, usage

MAC = Machine("mac", "Mac", "darwin")
PC = Machine("pc", "PC", "windows")
BOX = Machine("box", "Linux box", "linux")


def reads(conn, machine: Machine, accounts: list[list[str]]):
    """Which accounts a machine reads (one test ledger stands in for several machines)."""
    repo.set_machine_accounts(conn, machine.id, accounts)


def day_value(conn, provider, day="2026-10-10"):
    daily = views.daily(conn, day, day)
    return {p["provider"]: p["values"][0] for p in daily["providers"]}[provider]


def test_machine_sources_and_accounts_add_up(conn):
    mac, pc = make_settings(MAC), make_settings(PC)
    # Claude on the Mac through CodexBar and on the PC through its logs: added up.
    capture_into(conn, mac, SeriesKey("codexbar", "claude", "mac", Scope.MACHINE), [usage("2026-10-10", 100, cost=1)], "2026-10-19T10:00:00")
    capture_into(conn, pc, SeriesKey("claude-logs", "claude", "pc", Scope.MACHINE), [usage("2026-10-10", 40, cost=2)], "2026-10-19T10:00:00")
    # Two Codex accounts, each read on one machine: added up.
    capture_into(conn, mac, SeriesKey("codex-server", "codex", "acct-a", Scope.ACCOUNT), [usage("2026-10-10", 500)], "2026-10-19T10:00:00")
    capture_into(conn, pc, SeriesKey("codex-server", "codex", "acct-b", Scope.ACCOUNT), [usage("2026-10-10", 300)], "2026-10-19T10:00:00")
    reads(conn, MAC, [["codex", "acct-a"]])
    reads(conn, PC, [["codex", "acct-b"]])
    claude = day_value(conn, "claude")
    assert claude["tokens"] == 140 and claude["cost_usd"] == 3e-6
    codex = day_value(conn, "codex")
    assert codex["tokens"] == 800 and codex["basis"] == "account"


def test_logs_of_a_machine_that_reads_no_account_are_added(conn):
    mac, box = make_settings(MAC), make_settings(BOX)
    capture_into(conn, mac, SeriesKey("codex-server", "codex", "acct-a", Scope.ACCOUNT), [usage("2026-10-10", 500)], "2026-10-19T10:00:00")
    capture_into(conn, mac, SeriesKey("codex-logs", "codex", "mac", Scope.MACHINE), [usage("2026-10-10", 480, cost=9)], "2026-10-19T10:00:00")
    # The box runs Codex with an API key: no account figure counts its usage.
    capture_into(conn, box, SeriesKey("codex-logs", "codex", "box", Scope.MACHINE), [usage("2026-10-10", 3000, cost=50)], "2026-10-19T10:00:00")
    reads(conn, MAC, [["codex", "acct-a"]])
    reads(conn, BOX, [])
    codex = day_value(conn, "codex")
    assert codex["tokens"] == 3500 and codex["basis"] == "account" and codex["machine_tokens"] == 3480
    assert codex["cost_tokens"] == 3480  # every machine figure is priced

    split = views.machine_usage(conn, "2026-10-10", "2026-10-10", "mac")
    assert [(m["id"], m["tokens"], m["this_machine"]) for m in split["machines"]] == [("box", 3000, False), ("mac", 480, True)]
    assert split["other"]["tokens"] == 20  # account usage none of them accounts for
    assert split["machines"][0]["cost_usd"] == 0.0 and split["machines"][0]["providers"] == {"codex": 3000}


def test_a_ledger_from_before_sync_counts_as_before(conn):
    mac = make_settings(MAC)
    capture_into(conn, mac, SeriesKey("codex-server", "codex", "acct-a", Scope.ACCOUNT), [usage("2026-10-10", 500)], "2026-10-19T10:00:00")
    capture_into(conn, mac, SeriesKey("codex-logs", "codex", "mac", Scope.MACHINE), [usage("2026-10-10", 480)], "2026-10-19T10:00:00")
    conn.execute("UPDATE machines SET accounts = NULL")  # not recorded yet: reads every account
    assert day_value(conn, "codex")["tokens"] == 500


def test_source_health_per_machine(conn):
    mac = make_settings(MAC)
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
    health = views.source_health(conn, mac.machine.id)
    assert [(h["machine"], h["machine_label"], h["source"], h["ok"]) for h in health] == [
        ("mac", "mac", "codexbar", True),
        ("pc", "PC", "claude-logs", False),
    ]
    assert [(m["id"], m["this_machine"]) for m in views.machines(conn, mac.machine.id)] == [("pc", False)]


def test_dashboard_ranges(conn):
    mac = make_settings(MAC)
    capture_into(conn, mac, SeriesKey("codexbar", "claude", "mac", Scope.MACHINE), [usage("2026-01-05", 7), usage("2026-10-10", 100)], "2026-10-19T10:00:00")
    assert views.span(conn, "d30", "2026-10-20") == ("2026-09-21", "2026-10-20")
    assert views.span(conn, "all", "2026-10-20") == ("2026-01-05", "2026-10-20")
    view = views.dashboard(conn, mac, "d30", "2026-10-20")
    assert view["summary"]["tokens"] == 100 and view["status"]["today"] == "2026-10-20"
    assert set(view) == {"range", "status", "summary", "daily", "models", "machines", "peaks", "quota"}
