"""Several machines, one ledger: snapshots, merging them, restoring a machine's own."""

from __future__ import annotations

import json

import pytest

from tokenledger import ledger
from tokenledger.config import Machine, Settings
from tokenledger.models import CaptureResult, CostQuality, DayUsage, QuotaSample, Scope, SeriesKey
from tokenledger.sources.codex_server import LEGACY_ACCOUNT
from tokenledger.store import connect, migrate, repo
from tokenledger.sync import snapshot

from conftest import utc

TZ = "Europe/London"
MAC = Machine("mac", "Mac", "darwin")
PC = Machine("pc", "Windows PC", "windows")
NOW = "2026-10-20T12:00:00+00:00"


def make_settings(machine: Machine, tz: str = TZ, grace_days: int = 3) -> Settings:
    return Settings(timezone=tz, grace_days=grace_days, machine=machine)


def open_db(path):
    conn = connect(path)
    migrate(conn)
    return conn


def usage(day, total, model="*", cost=None):
    quality = CostQuality.LIST_PRICE if cost is not None else CostQuality.NONE
    return DayUsage(day=day, model=model, total=total, cost_micros=cost, cost_quality=quality)


def capture_into(conn, settings, series, rows, now, window=None):
    result = CaptureResult(
        series=series,
        rows=rows,
        window_start=window[0] if window else None,
        window_end=window[1] if window else None,
        tool_version="t",
        raw_name="t",
        raw=b"",
    )
    with repo.transaction(conn):
        repo.ensure_machine(conn, settings.machine, now)
        cid = repo.start_capture(conn, "usage", series.source, repo.series_id(conn, series), now)
        ledger.apply_capture(conn, result, settings=settings, now_utc=utc(now), capture_id=cid)
        repo.finish_capture(conn, cid, now, status="ok", tool_version="t")
        repo.set_machine_accounts(conn, settings.machine.id, repo.accounts_read(conn, LEGACY_ACCOUNT))


def logs(machine=PC.id):
    return SeriesKey("claude-logs", "claude", machine, Scope.MACHINE)


ACCOUNT = SeriesKey("codex-server", "codex", "acct-1", Scope.ACCOUNT)


@pytest.fixture
def pc(tmp_path):
    conn = open_db(tmp_path / "pc.sqlite")
    yield conn
    conn.close()


@pytest.fixture
def mac(tmp_path):
    conn = open_db(tmp_path / "mac.sqlite")
    yield conn
    conn.close()


def share(sender_conn, receiver_conn, *, sender=PC, receiver=MAC, taken_at=None, receiver_settings=None):
    snap = snapshot.build(sender_conn, make_settings(sender), app_version="test", taken_at=taken_at)
    snap = json.loads(json.dumps(snap))  # what travels is JSON
    with repo.transaction(receiver_conn):
        return snapshot.apply(
            receiver_conn, snap, sender=sender.id, settings=receiver_settings or make_settings(receiver), now=NOW
        )


def seed_pc(conn, settings=None):
    settings = settings or make_settings(PC)
    capture_into(conn, settings, logs(), [usage("2026-10-10", 100, cost=5), usage("2026-10-10", 100, "opus", cost=5)], "2026-10-11T10:00:00")
    capture_into(conn, settings, logs(), [usage("2026-10-10", 100, cost=5), usage("2026-10-10", 100, "opus", cost=5)], "2026-10-15T10:00:00")
    capture_into(conn, settings, logs(), [usage("2026-10-19", 40, cost=2)], "2026-10-19T10:00:00")
    capture_into(conn, settings, ACCOUNT, [usage("2026-10-10", 900), usage("2026-10-19", 300)], "2026-10-19T10:00:00")
    repo.insert_quota_samples(conn, [QuotaSample("claude", "a", "weekly", 10080, "2026-10-19T10:00:00Z", 12.5, None, "codexbar-quota")])


def rows_of(conn, source):
    return {
        (r["day"], r["model"]): (r["total"], r["state"])
        for r in conn.execute(
            "SELECT l.* FROM ledger l JOIN series s ON s.id = l.series_id WHERE s.source = ?", (source,)
        ).fetchall()
    }


def logs_row(conn, day):
    return conn.execute(
        "SELECT l.total, l.state FROM ledger l JOIN series s ON s.id = l.series_id "
        "WHERE s.source = 'claude-logs' AND l.day = ? AND l.model = '*'",
        (day,),
    ).fetchone()


# -- merging another machine's snapshot -----------------------------------------------------


def test_snapshot_is_mirrored_and_idempotent(pc, mac):
    seed_pc(pc)
    first = share(pc, mac, taken_at="2026-10-19T10:00:01.000000+00:00")
    assert first["inserted"] == 5 and first["captures"] == 4 and first["quota"] == 1 and not first["stale"]
    assert rows_of(mac, "claude-logs") == {
        ("2026-10-10", "*"): (100, "final"),
        ("2026-10-10", "opus"): (100, "final"),
        ("2026-10-19", "*"): (40, "open"),
    }
    assert rows_of(mac, "codex-server") == {("2026-10-10", "*"): (900, "final"), ("2026-10-19", "*"): (300, "open")}
    origins = {r["origin"] for r in mac.execute("SELECT origin FROM ledger")}
    assert all(o.startswith("sync:pc:capture:") for o in origins)
    machine = mac.execute("SELECT * FROM machines WHERE id = ?", (PC.id,)).fetchone()
    assert machine["label"] == "Windows PC" and machine["snapshot_at"] == "2026-10-19T10:00:01.000000+00:00"
    assert json.loads(machine["accounts"]) == [["codex", "acct-1"]]
    instance = snapshot.instance(pc)
    refs = {r["sync_ref"] for r in mac.execute("SELECT sync_ref FROM captures")}
    assert all(ref.startswith(f"pc:{instance}:") for ref in refs)
    quota = mac.execute("SELECT machine FROM quota_samples").fetchone()
    assert quota["machine"] == PC.id

    again = share(pc, mac, taken_at="2026-10-19T10:00:02.000000+00:00")
    assert (again["inserted"], again["updated"], again["unchanged"], again["captures"], again["quota"]) == (0, 0, 5, 0, 0)


def test_an_older_snapshot_changes_nothing(pc, mac):
    seed_pc(pc)
    share(pc, mac, taken_at="2026-10-19T10:00:05.000000+00:00")
    late = share(pc, mac, taken_at="2026-10-19T10:00:01.000000+00:00")
    assert late == {"stale": True, "applied_snapshot": "2026-10-19T10:00:05.000000+00:00"}


def test_later_changes_arrive(pc, mac):
    settings = make_settings(PC)
    seed_pc(pc, settings)
    share(pc, mac, taken_at="2026-10-19T10:00:01.000000+00:00")
    capture_into(pc, settings, logs(), [usage("2026-10-19", 70, cost=4)], "2026-10-23T09:00:00")  # grows, then settles
    result = share(pc, mac, taken_at="2026-10-23T09:00:01.000000+00:00")
    assert result["updated"] == 1
    assert tuple(logs_row(mac, "2026-10-19")) == (70, "final")


def test_a_final_row_keeps_its_value_when_the_owner_goes_back(pc, mac, tmp_path):
    seed_pc(pc)
    share(pc, mac, taken_at="2026-10-19T10:00:01.000000+00:00")
    # The PC's ledger is restored from an older backup and settles 10-10 again from fewer logs.
    older = open_db(tmp_path / "pc-restored.sqlite")
    capture_into(older, make_settings(PC), logs(), [usage("2026-10-10", 60, cost=3)], "2026-10-15T10:00:00")
    for n, taken in enumerate(("2026-10-20T10:00:00.000000+00:00", "2026-10-21T10:00:00.000000+00:00")):
        result = share(older, mac, taken_at=taken)
        assert result["kept_final"] == 1 and result["revisions"] == (1 if n == 0 else 0)  # recorded once
    assert tuple(logs_row(mac, "2026-10-10")) == (100, "final")
    rev = mac.execute("SELECT * FROM revisions WHERE kind = 'changed'").fetchone()
    assert (rev["observed_total"], rev["ledger_total"], rev["origin"]) == (60, 100, "sync:pc")


def test_a_correction_by_the_owner_is_taken_over(pc, mac):
    seed_pc(pc)
    share(pc, mac, taken_at="2026-10-19T10:00:01.000000+00:00")
    sid = repo.find_series(pc, logs())
    with repo.transaction(pc):  # what `accept` does on the PC
        repo.update_ledger_values(pc, sid, usage("2026-10-10", 120, cost=6), origin="manual:revision:1", now="2026-10-20T09:00:00+00:00")
    result = share(pc, mac, taken_at="2026-10-20T09:00:01.000000+00:00")
    assert result["updated"] == 1
    assert tuple(logs_row(mac, "2026-10-10")) == (120, "final")


def test_account_rows_from_two_machines(pc, mac, tmp_path):
    mac_settings = make_settings(MAC)
    # The Mac reads the same account itself and has already settled 10-10.
    capture_into(mac, mac_settings, ACCOUNT, [usage("2026-10-10", 950), usage("2026-10-19", 320)], "2026-10-19T11:00:00")
    capture_into(mac, mac_settings, ACCOUNT, [usage("2026-10-10", 950)], "2026-10-20T11:00:00")
    seed_pc(pc)  # the PC read 900 / 300 earlier
    result = share(pc, mac, taken_at="2026-10-19T10:00:01.000000+00:00")
    assert result["kept_final"] == 1 and result["revisions"] == 1  # 10-10 differs: recorded, kept
    assert rows_of(mac, "codex-server") == {("2026-10-10", "*"): (950, "final"), ("2026-10-19", "*"): (320, "open")}
    # A newer reading from another machine replaces an open day.
    laptop = Machine("laptop", "Laptop", "linux")
    other = open_db(tmp_path / "laptop.sqlite")
    capture_into(other, make_settings(laptop), ACCOUNT, [usage("2026-10-19", 400)], "2026-10-20T12:00:00")
    share(other, mac, sender=laptop, taken_at="2026-10-20T12:00:01.000000+00:00")
    assert rows_of(mac, "codex-server")[("2026-10-19", "*")] == (400, "open")


def test_refusals(pc, mac):
    seed_pc(pc)
    with pytest.raises(snapshot.SyncRefused, match="timezone"):
        share(pc, mac, receiver_settings=make_settings(MAC, tz="Asia/Shanghai"))
    with pytest.raises(snapshot.SyncRefused, match="grace_days"):
        share(pc, mac, receiver_settings=make_settings(MAC, grace_days=5))
    with pytest.raises(snapshot.SyncRefused, match="own"):
        share(pc, mac, sender=PC, receiver=PC)
    snap = snapshot.build(pc, make_settings(PC), app_version="t")
    with pytest.raises(snapshot.SyncRefused, match="shared as"):
        with repo.transaction(mac):
            snapshot.apply(mac, snap, sender="someone-else", settings=make_settings(MAC), now=NOW)
    snap["series"][0]["key"][2] = "someone-else"
    with pytest.raises(snapshot.SyncRefused, match="only share its own"):
        with repo.transaction(mac):
            snapshot.apply(mac, snap, sender=PC.id, settings=make_settings(MAC), now=NOW)
    snap = snapshot.build(pc, make_settings(PC), app_version="t")
    snap["series"].append({"key": ["codex-server", "codex", LEGACY_ACCOUNT, "account"], "rows": [], "revisions": []})
    with pytest.raises(snapshot.SyncRefused, match="may not write"):
        with repo.transaction(mac):
            snapshot.apply(mac, snap, sender=PC.id, settings=make_settings(MAC), now=NOW)


def test_a_snapshot_holds_only_what_this_machine_read(pc, mac):
    settings = make_settings(PC)
    seed_pc(pc, settings)
    # The unnamed account series of an older ledger stays here until it is folded.
    capture_into(pc, settings, SeriesKey("codex-server", "codex", LEGACY_ACCOUNT, Scope.ACCOUNT), [usage("2026-09-01", 5)], "2026-10-19T10:00:00")
    # Merged from the Mac: its own series and an account only the Mac reads.
    capture_into(mac, make_settings(MAC), logs(MAC.id), [usage("2026-10-10", 7)], "2026-10-19T10:00:00")
    capture_into(mac, make_settings(MAC), SeriesKey("codex-server", "codex", "acct-2", Scope.ACCOUNT), [usage("2026-10-10", 70)], "2026-10-19T10:00:00")
    share(mac, pc, sender=MAC, receiver=PC)
    snap = snapshot.build(pc, settings, app_version="t")
    assert {tuple(s["key"][:3]) for s in snap["series"]} == {("claude-logs", "claude", PC.id), ("codex-server", "codex", "acct-1")}
    assert len(snap["captures"]) == 5 and len(snap["quota"]) == 1  # the Mac's captures and samples stay out


def test_account_folds_reach_the_other_machines(pc, mac):
    settings = make_settings(PC)
    old = SeriesKey("codex-server", "codex", "acct-email", Scope.ACCOUNT)
    capture_into(pc, settings, old, [usage("2026-10-10", 900)], "2026-10-15T10:00:00")
    share(pc, mac, taken_at="2026-10-15T10:00:01.000000+00:00")
    with repo.transaction(pc):  # what capture does once the account id is known
        repo.merge_series(pc, repo.find_series(pc, old), repo.series_id(pc, ACCOUNT), NOW)
        repo.add_account_fold(pc, "codex-server", "codex", "acct-email", "acct-1")
    capture_into(pc, settings, ACCOUNT, [usage("2026-10-19", 30)], "2026-10-19T10:00:00")
    share(pc, mac, taken_at="2026-10-19T10:00:01.000000+00:00")
    names = [r["machine"] for r in mac.execute("SELECT machine FROM series WHERE source = 'codex-server'")]
    assert names == ["acct-1"]
    assert rows_of(mac, "codex-server") == {("2026-10-10", "*"): (900, "final"), ("2026-10-19", "*"): (30, "open")}


# -- restoring this machine's own snapshot --------------------------------------------------


def test_a_ledger_behind_its_shared_snapshot_gets_settled_days_back(pc, tmp_path):
    settings = make_settings(PC)
    seed_pc(pc, settings)
    shared = json.loads(json.dumps(snapshot.build(pc, settings, app_version="t")))
    # The data folder comes back from an older backup: 10-10 is still open, 10-19 missing,
    # and a later capture settles 10-10 again from logs that have been cleaned up since.
    behind = open_db(tmp_path / "behind.sqlite")
    capture_into(behind, settings, logs(), [usage("2026-10-10", 30, cost=1)], "2026-10-11T10:00:00")
    capture_into(behind, settings, logs(), [usage("2026-10-10", 30, cost=1)], "2026-10-21T10:00:00")
    capture_into(behind, settings, logs(), [usage("2026-10-20", 8, cost=1)], "2026-10-21T10:00:00")
    with repo.transaction(behind):
        stats = snapshot.restore(behind, shared, settings=settings, now=NOW)
    assert stats["updated"] == 1 and stats["inserted"] >= 2
    assert tuple(logs_row(behind, "2026-10-10")) == (100, "final")  # settled as it was settled then
    assert tuple(logs_row(behind, "2026-10-19")) == (40, "open")
    assert tuple(logs_row(behind, "2026-10-20")) == (8, "open")  # newer than the snapshot: kept
    rev = behind.execute("SELECT * FROM revisions WHERE kind = 'replaced'").fetchone()
    assert (rev["observed_total"], rev["ledger_total"]) == (100, 30)
    with pytest.raises(snapshot.SyncRefused, match="another machine"):
        snapshot.restore(behind, shared, settings=make_settings(MAC), now=NOW)
