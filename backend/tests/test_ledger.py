from __future__ import annotations

from tokenledger import ledger
from tokenledger.models import CostQuality, DayUsage, State
from tokenledger.store import repo

from conftest import SERIES, capture, run_capture, usage, utc


def row(conn, day, model="*"):
    sid = repo.series_id(conn, SERIES)
    return repo.ledger_row(conn, sid, day, model)


def revisions(conn):
    return conn.execute("SELECT kind, day, observed_total, ledger_total FROM revisions ORDER BY id").fetchall()


def test_open_window_is_today_and_two_previous_days(settings):
    today = ledger.local_today(settings, utc("2026-09-27T12:00:00"))
    assert ledger.is_open("2026-09-27", today, 3)
    assert ledger.is_open("2026-09-25", today, 3)
    assert not ledger.is_open("2026-09-24", today, 3)


def test_day_uses_ledger_timezone(settings):
    # 23:30 UTC on 26 Sep is 00:30 on 27 Sep in London (BST)
    assert ledger.local_today(settings, utc("2026-09-26T23:30:00")).isoformat() == "2026-09-27"


def test_open_day_is_overwritten_then_frozen(conn, settings):
    run_capture(conn, settings, capture([usage("2026-09-27", 100)]), "2026-09-27T10:00:00")
    assert row(conn, "2026-09-27")["state"] == State.OPEN
    run_capture(conn, settings, capture([usage("2026-09-27", 150)]), "2026-09-28T10:00:00")
    assert row(conn, "2026-09-27")["total"] == 150
    # a decrease inside the grace period is accepted (source corrected itself)
    run_capture(conn, settings, capture([usage("2026-09-27", 140)]), "2026-09-29T10:00:00")
    assert row(conn, "2026-09-27")["total"] == 140
    stats = run_capture(conn, settings, capture([usage("2026-09-27", 90)]), "2026-09-30T10:00:00")
    r = row(conn, "2026-09-27")
    assert r["state"] == State.FINAL and r["total"] == 140
    assert stats.revisions == 1
    assert [tuple(x) for x in revisions(conn)] == [("changed", "2026-09-27", 90, 140)]


def test_old_day_first_seen_is_final_immediately(conn, settings):
    run_capture(conn, settings, capture([usage("2026-08-01", 500)]), "2026-09-27T10:00:00")
    assert row(conn, "2026-08-01")["state"] == State.FINAL


def test_repeated_capture_same_values_is_idempotent(conn, settings):
    rows = [usage("2026-08-01", 500), usage("2026-09-27", 10)]
    run_capture(conn, settings, capture(rows), "2026-09-27T10:00:00")
    stats = run_capture(conn, settings, capture(rows), "2026-09-27T11:00:00")
    assert stats.inserted == 0 and stats.updated == 0 and stats.revisions == 0
    assert conn.execute("SELECT COUNT(*) FROM ledger").fetchone()[0] == 2


def test_final_divergence_recorded_once_per_distinct_value(conn, settings):
    run_capture(conn, settings, capture([usage("2026-08-01", 500)]), "2026-09-27T10:00:00")
    for hour, value in [(11, 300), (12, 300), (13, 320), (14, 500)]:
        run_capture(conn, settings, capture([usage("2026-08-01", value)]), f"2026-09-27T{hour}:00:00")
    assert row(conn, "2026-08-01")["total"] == 500
    # returning to the ledger's own value is not a new claim
    assert [x["observed_total"] for x in revisions(conn)] == [300, 320]


def test_flip_flopping_source_is_recorded_once_per_value(conn, settings):
    run_capture(conn, settings, capture([usage("2026-08-01", 66)]), "2026-09-27T10:00:00")
    for hour, value in [(11, 51), (12, 66), (13, 51), (14, 66), (15, 51)]:
        run_capture(conn, settings, capture([usage("2026-08-01", value)]), f"2026-09-27T{hour}:00:00")
    assert [x["observed_total"] for x in revisions(conn)] == [51]
    assert row(conn, "2026-08-01")["total"] == 66


def test_missing_day_inside_window_keeps_value(conn, settings):
    window = ("2026-07-01", "2026-09-27")
    run_capture(conn, settings, capture([usage("2026-08-01", 500)], window), "2026-09-27T10:00:00")
    stats = run_capture(conn, settings, capture([], window), "2026-09-27T11:00:00")
    run_capture(conn, settings, capture([], window), "2026-09-27T12:00:00")
    assert row(conn, "2026-08-01")["total"] == 500
    assert stats.missing == ["2026-08-01/*"]
    assert [tuple(x) for x in revisions(conn)] == [("missing", "2026-08-01", None, 500)]


def test_day_outside_window_is_not_missing(conn, settings):
    run_capture(conn, settings, capture([usage("2025-01-01", 5)], ("2025-01-01", "2025-01-01")), "2026-09-27T10:00:00")
    stats = run_capture(conn, settings, capture([], ("2026-01-01", "2026-09-27")), "2026-09-27T11:00:00")
    assert stats.missing == [] and revisions(conn) == []


def test_partial_day_is_not_frozen_by_a_late_capture(conn, settings):
    # captured at lunchtime, then nothing for two weeks: the settling capture takes the full day
    run_capture(conn, settings, capture([usage("2026-09-27", 100)]), "2026-09-27T12:00:00")
    stats = run_capture(conn, settings, capture([usage("2026-09-27", 400)]), "2026-10-11T12:00:00")
    r = row(conn, "2026-09-27")
    assert (r["total"], r["state"]) == (400, State.FINAL)
    assert stats.finalized == 1 and stats.revisions == 0
    # once settled, later values are only recorded
    run_capture(conn, settings, capture([usage("2026-09-27", 500)]), "2026-10-12T12:00:00")
    assert row(conn, "2026-09-27")["total"] == 400
    assert [x["observed_total"] for x in revisions(conn)] == [500]


def test_settling_capture_keeps_ledger_when_source_shrank(conn, settings):
    run_capture(conn, settings, capture([usage("2026-09-27", 400)]), "2026-09-28T12:00:00")
    run_capture(conn, settings, capture([usage("2026-09-27", 250)]), "2026-10-11T12:00:00")
    r = row(conn, "2026-09-27")
    assert (r["total"], r["state"]) == (400, State.FINAL)
    assert [tuple(x) for x in revisions(conn)] == [("changed", "2026-09-27", 250, 400)]


def test_settling_capture_with_day_gone_freezes_and_records_missing(conn, settings):
    window = ("2026-09-01", "2026-10-11")
    run_capture(conn, settings, capture([usage("2026-09-27", 400)], window), "2026-09-27T12:00:00")
    stats = run_capture(conn, settings, capture([], window), "2026-10-11T12:00:00")
    r = row(conn, "2026-09-27")
    assert (r["total"], r["state"]) == (400, State.FINAL)
    assert stats.finalized == 1
    assert [tuple(x) for x in revisions(conn)] == [("missing", "2026-09-27", None, 400)]


def test_settling_decides_per_day_on_the_total_row(conn, settings):
    first = [usage("2026-09-27", 100), usage("2026-09-27", 60, model="a"), usage("2026-09-27", 40, model="b")]
    run_capture(conn, settings, capture(first), "2026-09-27T12:00:00")
    # total grew; model "a" was re-attributed downwards, model "c" is new
    later = [usage("2026-09-27", 150), usage("2026-09-27", 50, model="a"), usage("2026-09-27", 40, model="b"),
             usage("2026-09-27", 60, model="c")]
    run_capture(conn, settings, capture(later), "2026-10-11T12:00:00")
    totals = {m: row(conn, "2026-09-27", m)["total"] for m in ("*", "a", "b", "c")}
    assert totals == {"*": 150, "a": 50, "b": 40, "c": 60}
    assert all(row(conn, "2026-09-27", m)["state"] == State.FINAL for m in totals)


def test_other_series_stay_open_until_their_own_capture(conn, settings):
    from tokenledger.models import Scope, SeriesKey

    run_capture(conn, settings, capture([usage("2026-09-27", 100)]), "2026-09-27T12:00:00")
    other = capture([usage("2026-10-11", 5)])
    other.series = SeriesKey("codex-server", "codex", "account", Scope.ACCOUNT)
    run_capture(conn, settings, other, "2026-10-11T12:00:00")
    assert row(conn, "2026-09-27")["state"] == State.OPEN  # waits for its own settling capture


def test_missing_total_row_fails_fast(conn, settings):
    import pytest

    with pytest.raises(ValueError, match="all-model total"):
        run_capture(conn, settings, capture([usage("2026-09-27", 5, model="a")]), "2026-09-27T12:00:00")


def test_backfill_replaces_final_and_records_old_value(conn, settings):
    run_capture(conn, settings, capture([usage("2026-08-01", 500)]), "2026-09-27T10:00:00")
    new = [
        DayUsage(day="2026-08-01", model="*", total=700, cost_micros=9, cost_quality=CostQuality.RECOMPUTED),
        usage("2026-09-27", 1),
    ]
    with repo.transaction(conn):
        stats = ledger.apply_backfill(
            conn, SERIES, new, settings=settings, now_utc=utc("2026-09-27T12:00:00"), capture_id=None, origin="backfill:x"
        )
    r = row(conn, "2026-08-01")
    assert r["total"] == 700 and r["origin"] == "backfill:x" and r["state"] == State.FINAL
    assert stats.skipped_open == 1 and stats.updated == 1
    assert [tuple(x) for x in revisions(conn)] == [("replaced", "2026-08-01", 700, 500)]
    # the source still saying what it said at backfill time is not news
    s2 = run_capture(conn, settings, capture([usage("2026-08-01", 500)]), "2026-09-27T13:00:00")
    assert s2.revisions == 0 and row(conn, "2026-08-01")["total"] == 700
    # the source later losing the day is
    s3 = run_capture(conn, settings, capture([], ("2026-07-01", "2026-09-27")), "2026-09-27T14:00:00")
    assert s3.revisions == 1 and row(conn, "2026-08-01")["total"] == 700


def test_backfill_only_day_is_not_reported_missing(conn, settings):
    with repo.transaction(conn):
        ledger.apply_backfill(
            conn, SERIES, [usage("2026-07-05", 42)], settings=settings, now_utc=utc("2026-09-27T12:00:00"),
            capture_id=None, origin="backfill:x",
        )
    window = ("2026-07-01", "2026-09-27")
    assert run_capture(conn, settings, capture([], window), "2026-09-27T13:00:00").revisions == 0
    # if the source ever reports that day, the difference is recorded; the ledger keeps its value
    assert run_capture(conn, settings, capture([usage("2026-07-05", 40)], window), "2026-09-27T14:00:00").revisions == 1
    assert row(conn, "2026-07-05")["total"] == 42


def test_duplicate_rows_fail_fast(conn, settings):
    import pytest

    with pytest.raises(ValueError):
        run_capture(conn, settings, capture([usage("2026-08-01", 1), usage("2026-08-01", 2)]), "2026-09-27T10:00:00")
    assert conn.execute("SELECT COUNT(*) FROM ledger").fetchone()[0] == 0
