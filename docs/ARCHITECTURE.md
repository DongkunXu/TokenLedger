# TokenLedger architecture

TokenLedger is an append-only ledger of AI usage. It reads daily token counts and estimated cost from CodexBar, the Codex logs on this Mac and the Codex server, and writes them into a local database. Once a day's grace period is over the day is final; if a source later shrinks or loses that day, the ledger keeps what it had.

## Layout

```
TokenLedger/
├── backend/                 Python package tokenledger (managed with uv)
│   ├── tokenledger/
│   │   ├── config.py        Paths, timezone, grace period, machine id (data/settings.json)
│   │   ├── models.py        Core types: DayUsage, SeriesKey, CaptureResult, QuotaSample
│   │   ├── store/           SQLite connection and migrations (db.py), row access (repo.py)
│   │   ├── ledger.py        Settling rules: write, freeze, revisions
│   │   ├── capture.py       Runs the sources, archives raw output, merges into the ledger
│   │   ├── rawstore.py      gzip archive of raw output
│   │   ├── sources/         Data source adapters (one interface)
│   │   ├── backfill/        One-off backfill from old CodexBar caches
│   │   ├── pricing.py       List-price cost, using CodexBar's price table and rules
│   │   ├── corrections.py   Adopting a source's new value by hand
│   │   ├── views.py         Queries and summaries for the API and MCP
│   │   ├── api/             FastAPI: JSON API + the built frontend
│   │   ├── mcp_server.py    MCP tools
│   │   └── cli.py           Command line entry point
│   └── tests/
├── frontend/                Vite + React + TypeScript; every chart is hand-written SVG
│   └── src/
│       ├── api/             API types and requests
│       ├── panels/          The eight panels (00 Total … 07 Sources)
│       ├── components/      Panel frame, rail, top bar, toggles, pixel meters, tooltips, fun equivalents
│       ├── i18n/strings.ts  Every UI string (Chinese / English)
│       ├── theme/           Light and dark design tokens, theme switching
│       ├── styles/          Page frame (app.css) and panel internals (panels.css)
│       ├── assets/bg.jpg    Background (made by scripts/grade-background.py)
│       └── lib/             Formatting, data shaping, sizing and motion, fun equivalents table (fun.ts)
├── scripts/                 Every build, test and run goes through these
├── data/                    Ledger data (ignored by git)
├── docs/                    This file, README images and logo
└── research/                Data source investigation
```

## Data flow

```
CodexBar CLI ────────┐
Codex logs ──────────┤
Codex server ────────┼─> sources/*  ─> CaptureResult ─> ledger.apply_capture() ─> SQLite ─> views ─> API / MCP ─> frontend
CodexBar quota hist. ┘                     │
                                          └─> data/raw/ raw output archive
Old CodexBar caches ─> backfill/ ─> ledger.apply_backfill()
```

## Source interface

`sources/base.py` defines two kinds of source:

```python
class UsageSource(Protocol):
    id: str
    def collect(self, ctx: SourceContext) -> list[CaptureResult]: ...

class QuotaSource(Protocol):
    id: str
    def collect(self, ctx: SourceContext) -> QuotaCapture: ...
```

Sources only read and parse; they never touch the database. When parsing finds data it cannot trust (an unfinished scan, fields that do not line up, a total that is not the sum of its parts) the source raises `SourceError`. That capture is recorded as failed and the other sources still run.

A series is identified by four fields:

| Field | Examples |
|---|---|
| source | `codexbar`, `codex-logs`, `codex-server`, a future `claude-logs` |
| provider | `claude`, `codex`, `antigravity` |
| machine | This Mac's id; `account` for account-wide data |
| scope | `machine` (local logs) or `account` (the whole account) |

Current sources:

| id | Reads | Notes |
|---|---|---|
| `codexbar` | `codexbar cost --provider <p> --days 365 --format json` for `claude` and `antigravity` | `CODEX_HOME` and `CLAUDE_CONFIG_DIR` are removed from the environment first: CodexBar keeps a single shared cache, and running its CLI against other folders rewrites the menu-bar app's cache. Fails when the ledger timezone differs from CodexBar's. Read Codex too before 2026-10-04, see below |
| `codex-logs` | `~/.codex/sessions/**/*.jsonl` and `~/.codex/archived_sessions/*.jsonl`, read only; prices from CodexBar's cached table `~/Library/Caches/CodexBar/model-pricing/models-dev-v1.json` | Each request is counted once, rules below. The archived raw output is the per-day, per-model summary, which `accept` rebuilds rows from. Fails when the price table is missing |
| `codex-server` | Starts the Codex CLI in `app-server` mode and sends `initialize` and the read-only `account/usage/read`. The CLI is looked up in the ChatGPT app (`Contents/Resources/codex-cli/bin/codex`, earlier `Contents/Resources/codex`), then as `codex` on PATH | Daily token totals only; the server's day boundary looks like UTC. If a ChatGPT update moves the CLI again, the capture fails and lists the paths it tried; add the new one to `BINARY_CANDIDATES` |
| `codexbar-quota` | `~/Library/Application Support/com.steipete.codexbar/history/*.json`, read only | CodexBar compacts this history; the ledger keeps every sample it has seen |

## Codex logs (codex-logs)

Before 2026-10-04 Codex data for this Mac came from CodexBar. CodexBar counts the running total a forked conversation inherits as new usage on the day of the fork: in a forked log file, the first `token_count` event already carries the whole original conversation (on 4 October CodexBar reported 542M, the logs held 67.6M, and the server showed 13.3M while still catching up). The overcount on 14 September had the same cause. `sources/codex_logs.py` now reads the logs itself:

- Newer Codex versions write one `token_usage_record` per request, with a unique `response_id`. Files that contain such records use only them, deduplicated by `response_id` across all files, so history copied into another file is not counted again
- Older files without those records use `token_count` events: the difference between two consecutive running totals is one request. The first event in a file only counts its own `last_token_usage`, so an inherited running total is not counted. These events are deduplicated across files by timestamp and running total
- Each request goes to the day of its timestamp in the ledger timezone, is priced on its own, and is then summed per day and model; `total` is input + output, as in CodexBar
- The newer records also include requests such as context compaction, so the counts are slightly higher than CodexBar's (1% to 3% a day) and closer to the server's

The CodexBar Codex series is no longer captured; the ledger keeps its history (migration 2 settled its remaining open rows). The dashboard picks per day: `codex-logs` when the logs have that day, otherwise the CodexBar record from back then (`views.MACHINE_SOURCE_ORDER`).

## Token fields

The two providers count input differently, so everything is normalized before it is stored:

| Field | Claude (CodexBar) | Codex (logs, CodexBar before) |
|---|---|---|
| `input_uncached` | inputTokens | inputTokens − cacheReadTokens |
| `cache_read` | cacheReadTokens | cacheReadTokens |
| `cache_write` | cacheCreationTokens | empty |
| `output` | outputTokens | outputTokens |
| `reasoning` | empty | reasoningTokens (already in output, display only) |
| `total` | sum of the four | inputTokens + outputTokens |

Each day has a `model = "*"` row with the all-model total (with its parts), plus one row per model (CodexBar's model rows only carry a total and cost; `codex-logs` model rows carry every part). The Codex server only gives `total`.

## Database

| Table | Holds |
|---|---|
| `series` | Series definitions |
| `ledger` | One row per series, day and model: token fields, cost (integer micro-dollars), cost quality, `open`/`final`, origin (`capture:<id>`, `backfill:*`, `manual:revision:<id>`) |
| `revisions` | What a source said about a final day that differs: `changed`, `missing`, and old values replaced by a backfill or by hand (`replaced`) |
| `captures` | Time, status, tool version, raw archive path, stats and error of every capture and backfill |
| `quota_samples` | Quota percentage samples |
| `machines`, `meta` | Machine info, schema version |

Migrations live in the `MIGRATIONS` list in `store/db.py`; they are only ever appended.

## Settling rules

- Days are bucketed in the ledger timezone, which must match CodexBar's. On first use it is taken from CodexBar's setting, or from this Mac when CodexBar has none (`data/settings.json`)
- Day D is `open` on D, D+1 and D+2 and every capture overwrites it, smaller values included (a source correcting itself within the grace period)
- After the grace period, the first capture of a series settles it, judged on the day's all-model row (`model = "*"`):
  - The source's total is not smaller than the ledger's: take all of the source's rows for that day, then freeze. Late usage gets in, and a capture made long after the day does not freeze a half-counted number
  - The source's total is smaller, or the day is gone: keep the ledger's rows and freeze; the source's version goes into `revisions`. This usually means logs were deleted
- Each series settles on its own: a series that was not captured keeps its open rows until its own next capture
- A day first seen after its grace period is written as `final` straight away
- After `final`, a different value from a source only goes into `revisions`, and the same claim is recorded once
- "The same claim" means any value the source has ever given for that day: ledger values written by a capture, the source's values at the time of a backfill replacement, revisions recorded before. Rows that only a backfill added, which the source never had, do not count as changed when the source does not report them. A source that flips between two values (CodexBar's Codex 14 September did) leaves just two records
- `DayUsage` checks that cost and cost quality agree when it is built, so contradictions such as "has a cost but marked unpriced" cannot be stored; every source day must include the all-model row

## How often to capture

Accuracy does not depend on capture frequency, as long as a capture happens while the logs still exist. Capturing by hand ("Capture" on the dashboard, or through MCP) every week or two is enough; capture before deleting conversations.

`scripts/install-agent.sh` installs a launchd job, but when the project lives in `~/Documents`, macOS does not let bash started by launchd read that folder (exit 126, Operation not permitted). For a scheduled capture, keep the project outside `~/Documents` (and the other protected folders).

## Cost

| Quality | Meaning |
|---|---|
| `list_price` | Computed at API list price: by CodexBar (including amounts saved in its old caches), or by `codex-logs` per request with CodexBar's price table and rules |
| `recomputed` | Recomputed during a backfill with CodexBar's pricing rules |
| `unpriced` | Has tokens, but the model has no price (for example codex-auto-review) |
| `none` | The source has no cost (Codex server) |

A cost without a basis is left empty, never guessed.

Pricing rules are in `pricing.py`. Codex long context: when a request's input passes the threshold, the whole request is priced at `context_over_200k`. The threshold is 272K for the gpt-5 family and 200K for later models. It was worked out from CodexBar's results; with it, every day and model from 2026-09-13 to 10-03 matches CodexBar's cost (except the overcounted 14 September).

## Backfill from old caches

`tokenledger backfill` reads old CodexBar caches saved in `data/legacy/codexbar-cache-*/` (the originals are in `~/Library/Caches/CodexBar` and can be cleared at any time, so copy them into the project first). The rules and how they were checked are in `research/README.md`:

- Claude: a day's value = CodexBar's current value + requests that exist only in the old caches (deduplicated by `messageId + requestId`). v4/v5 requests carry CodexBar's cost from back then; v2 has no cost and is recomputed using the share of 1-hour cache writes worked out from requests v2 and v4 have in common
- Codex: for days CodexBar no longer reports, take the per-day total of the newest old cache covering that day (v10 over v9; v9 also counted history copied by forked sessions); cost is recomputed per request with the long-context rule and scaled to the total

## Manual corrections

`tokenledger accept <revision_id>` (or MCP `accept_revision`) re-parses the full row from the raw archive behind that revision and writes it into the ledger; the old value is kept as `replaced`. `missing` revisions cannot be accepted: the ledger does not delete history.

## Dashboard rules (views.py)

- Claude: the local data of every machine, added up
- Codex: account-wide tokens on days that have them, local data otherwise. The server's data is complete only half a day to a day later, so open days take the larger of the two. Final days use account data only, since the server's day boundary is UTC and mixing it with local data would count usage around midnight twice. Cost only comes from local data; `cost_tokens` tells how many tokens the cost covers
- When one machine has several local sources for a provider, each day takes the first source in `MACHINE_SOURCE_ORDER` that has the day; sources that are no longer captured (`RETIRED_SOURCES`) do not appear in the source health list
- Quota peaks: weekly windows are grouped by reset time and take the highest value, skipping windows that stayed at 0% (Codex's weekly window rolls, and when idle every sample says "resets in 7 days"); 5-hour windows take the highest of each local day

## HTTP API

Listens on 127.0.0.1 only. Everything except `POST /api/capture` is read-only.

| Path | Returns |
|---|---|
| `GET /api/status` | Machine, timezone, ledger row counts, and each source's latest attempt (`sources`: ok, time, error, last success) |
| `GET /api/summary?start&end` | Totals per provider, cost coverage, token makeup, peak day, streaks |
| `GET /api/daily?start&end` | Tokens, cost, quality and open flag per day and provider |
| `GET /api/models?start&end` | Totals per model (local data) |
| `GET /api/quota?start&end&max_points` | Raw quota samples (thinned) |
| `GET /api/quota/peaks?start&end` | Quota peaks |
| `GET /api/quota/now` | Latest reading and reset time of every quota window |
| `GET /api/revisions?limit`, `GET /api/captures?limit` | Revisions and captures |
| `POST /api/capture` | Capture now |

Interactive docs at `/api/docs`.

## Dashboard

Console style: square corners everywhere, large areas only black, white and grey; Claude is orange and Codex blue, and orange is also the only accent. Titles and numbers use Chakra Petch, small marks such as panel numbers and code tags use the pixel font Silkscreen, and data detail uses JetBrains Mono; all three fonts are bundled, so the page works offline. Each panel is a thin frosted-glass sheet with grain and an edge light that follows the pointer, over a colour-graded photo that the CSS darkens or lightens per theme.

There is no chart library; every chart is SVG drawn by React: stacked daily token bars, the weekly cost block matrix, the heatmap, monthly LED bars and segmented quota bars. Charts that fill their panel read the real size of their area with `useSize` before drawing, so panels of different heights in one row line up and everything reflows when the window changes size.

The interface follows the browser's language (Chinese or English) until you pick one, and the system theme until you pick light or dark; both choices are stored in the browser.

`index.html` is served with `Cache-Control: no-cache`, so a rebuild shows up on refresh; hashed asset files are cached as usual.

## Dashboard server lifecycle

`tokenledger serve` (`api/server.py`) is the server; `tokenledger dashboard` (`dashboard.py`) manages it:

- `dashboard start` launches the server in its own session (`start_new_session`), detached from the terminal that started it, and returns once `/api/status` answers. Only one runs at a time; a port taken by another program is an error
- On start the server writes `.build/run/dashboard.json` (pid, port, start time, idle limit) and removes it on exit. The management commands find the server through it and first check that the pid really is a TokenLedger server before sending signals; a state file left by a crash is detected and removed
- Every exit is graceful: SIGTERM (`dashboard stop`), Ctrl+C, SIGHUP (its terminal closed), idle timeout. uvicorn re-raises SIGINT/SIGTERM after handling them, so the server installs Python-level handlers first to make sure cleanup and logging finish. `stop` force-kills only after 10 seconds
- Idle time is wall-clock time and counts only page and API requests; the health check of `dashboard status` sends an `X-TokenLedger-Probe` header and does not count, or checking the status would keep it alive forever
- The log is `.build/run/dashboard.log`, overwritten on every start, so only the latest run is kept

## MCP tools

`scripts/mcp.sh` serves over stdio: `ledger_status`, `usage_summary`, `daily_usage`, `model_breakdown`, `quota_history`, `quota_peaks`, `quota_now`, `list_revisions`, `list_captures`, `dashboard_status` (read only), `run_capture`, `accept_revision` (write to the ledger), and `dashboard_start`, `dashboard_stop` (control the dashboard server).

## Windows, later

Windows has no CodexBar. The plan is a `claude-logs` source that parses `%USERPROFILE%\.claude\projects\**\*.jsonl` directly, with CodexBar's rules: deduplicate by `message.id + requestId`, price with models.dev prices, tell 5-minute and 1-hour cache writes apart. Its `CaptureResult` carries its own machine id and goes through the same `ledger.apply_capture()`. How the data gets from Windows back to the Mac (an export file in a shared folder, or calling this Mac's API over Tailscale) is still open. The parser can be checked against CodexBar's results on a Mac first.

## Running cost and output

See "Where things live" in the README.
