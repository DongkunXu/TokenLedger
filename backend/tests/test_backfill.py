from __future__ import annotations

import json

import pytest

from tokenledger.backfill.codexbar_legacy import claude_backfill, codex_backfill
from tokenledger.backfill.pricing import Catalog, codex_request_cost_usd
from tokenledger.models import DAY_TOTAL, CostQuality, DayUsage

CATALOG = Catalog(
    providers={
        "anthropic": {"models": {"claude-x": {"cost": {"input": 5, "output": 25, "cache_read": 0.5, "cache_write": 6.25}}}},
        "openai": {
            "models": {
                "gpt-x": {
                    "cost": {"input": 4, "output": 20, "cache_read": 0.4,
                             "context_over_200k": {"input": 8, "output": 30, "cache_read": 0.8}}
                }
            }
        },
    }
)


def claude_row(mid, day, *, inp=10, cr=100, cc=20, out=5, cost_nanos=0, priced=False, cc1h=None):
    r = {"messageId": mid, "requestId": "r" + mid, "dayKey": day, "model": "claude-x", "input": inp,
         "cacheRead": cr, "cacheCreate": cc, "output": out, "costNanos": cost_nanos, "costPriced": priced}
    if cc1h is not None:
        r["cacheCreate1h"] = cc1h
    return r


def write_cache(folder, stem, rows_by_file=None, days=None, codex_rows=None):
    files = {}
    for i, rows in enumerate(rows_by_file or []):
        files[f"/f{i}.jsonl"] = {"claudeRows": rows}
    for i, rows in enumerate(codex_rows or []):
        files[f"/c{i}.jsonl"] = {"codexRows": rows}
    (folder / f"{stem}.json").write_text(json.dumps({"days": days or {}, "files": files}))


@pytest.fixture
def claude_caches(tmp_path):
    # m1 still exists in the current cache; m2 appears twice in v2 (copied history);
    # m3 only in v4 (with CodexBar's stored cost); m4 shared by v2 and v4 for calibration.
    write_cache(tmp_path, "claude-v6", [[claude_row("m1", "2026-07-01")]])
    write_cache(tmp_path, "claude-v5", [])
    write_cache(
        tmp_path, "claude-v4",
        [[claude_row("m3", "2026-07-01", cost_nanos=2_000_000_000, priced=True, cc1h=20),
          claude_row("m4", "2026-06-01", cc1h=15)]],
    )
    write_cache(
        tmp_path, "claude-v2",
        [[claude_row("m1", "2026-07-01"), claude_row("m2", "2026-06-01"), claude_row("m4", "2026-06-01")],
         [claude_row("m2", "2026-06-01")]],
    )
    return tmp_path


def test_claude_adds_only_requests_missing_from_current(claude_caches):
    base = [DayUsage(day="2026-07-01", model=DAY_TOTAL, total=135, input_uncached=10, cache_read=100,
                     cache_write=20, output=5, cost_micros=1_000_000, cost_quality=CostQuality.LIST_PRICE)]
    result = claude_backfill(claude_caches, base, CATALOG)
    assert result.added_requests == 3  # m2 once, m3, m4
    assert result.added_by_cache == {"claude-v4": 2, "claude-v2": 1}
    assert result.one_hour_share == pytest.approx(0.75)  # m4: 15 of 20 cache-write tokens
    rows = {(r.day, r.model): r for r in result.rows}
    july = rows[("2026-07-01", DAY_TOTAL)]
    assert july.total == 270  # current 135 + m3 135
    assert july.cost_micros == 3_000_000 and july.cost_quality == CostQuality.LIST_PRICE
    june = rows[("2026-06-01", DAY_TOTAL)]
    assert june.total == 270 and june.cost_quality == CostQuality.RECOMPUTED
    # m2 recomputed: 10*5 + 100*0.5 + 5*25 = 225 (per M) + cache writes 20 * (0.25*6.25 + 0.75*10)
    expected_m2 = (10 * 5 + 100 * 0.5 + 5 * 25 + 20 * (0.25 * 6.25 + 0.75 * 10)) / 1e6
    assert june.cost_micros == round(expected_m2 * 1e6) * 2  # m2 and m4 are identical requests


def test_codex_prefers_newest_day_totals_and_skips_reported_days(tmp_path):
    row = lambda day, inp, cached, out, turn: {"day": day, "model": "gpt-x", "input": inp, "cached": cached, "output": out, "turnID": turn, "eventIndex": 0}
    write_cache(tmp_path, "codex-v10", days={"2026-07-02": {"gpt-x": [1000, 900, 10]}},
                codex_rows=[[row("2026-07-02", 1000, 900, 10, "t1")]])
    write_cache(tmp_path, "codex-v9", days={"2026-07-02": {"gpt-x": [5000, 4500, 50]}, "2026-06-20": {"gpt-x": [300, 0, 3]}},
                codex_rows=[[row("2026-06-20", 300, 0, 3, "t2")]])
    write_cache(tmp_path, "codex-v6", codex_rows=[[row("2026-06-12", 100, 50, 1, "t3"), row("2026-09-20", 7, 0, 0, "t4")]])
    rows = codex_backfill(tmp_path, {"2026-09-20"}, CATALOG)
    totals = {r.day: r for r in rows if r.model == DAY_TOTAL}
    assert set(totals) == {"2026-06-12", "2026-06-20", "2026-07-02"}
    assert totals["2026-07-02"].total == 1010  # v10 wins over v9's inflated 5050
    assert (totals["2026-07-02"].input_uncached, totals["2026-07-02"].cache_read) == (100, 900)
    assert totals["2026-06-12"].total == 101  # rows-only day
    assert totals["2026-07-02"].cost_quality == CostQuality.RECOMPUTED


def test_codex_long_context_rates_apply_to_whole_request():
    small = codex_request_cost_usd(CATALOG, "gpt-x", 272_000, 0, 0)
    large = codex_request_cost_usd(CATALOG, "gpt-x", 272_001, 0, 0)
    assert small == pytest.approx(272_000 * 4 / 1e6)
    assert large == pytest.approx(272_001 * 8 / 1e6)
    assert codex_request_cost_usd(CATALOG, "unknown", 1, 0, 0) is None
