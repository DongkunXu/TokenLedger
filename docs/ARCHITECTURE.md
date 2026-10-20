# TokenLedger architecture

TokenLedger is an append-only ledger of AI usage. It reads daily token counts and estimated cost from CodexBar (on a Mac), the Claude Code and Codex logs on the machine and the Codex server, and writes them into a local database. Several machines can share one ledger through a cloud ledger on Cloudflare (see [MULTI_MACHINE.md](MULTI_MACHINE.md)). Once a day's grace period is over the day is final; if a source later shrinks or loses that day, the ledger keeps what it had.

## Layout

```
TokenLedger/
├── backend/                 Python package tokenledger (managed with uv)
│   ├── tokenledger/
│   │   ├── config.py        Paths, timezone, grace period, machine id, WSL mode (data/settings.json)
│   │   ├── homes.py         Where Claude Code and Codex keep their logs on this computer (incl. WSL)
│   │   ├── catalog.py       models.dev price catalog: CodexBar's, or an own copy built the same way
│   │   ├── models.py        Core types: DayUsage, SeriesKey, CaptureResult, QuotaSample
│   │   ├── store/           SQLite connection and migrations (db.py), row access (repo.py)
│   │   ├── ledger.py        Settling rules: write, freeze, revisions
│   │   ├── capture.py       Runs the sources, archives raw output, merges into the ledger
│   │   ├── rawstore.py      gzip archive of raw output
│   │   ├── sources/         Data source adapters (one interface)
│   │   ├── backfill/        One-off backfill from old CodexBar caches
│   │   ├── pricing.py       List-price cost, using CodexBar's price table and rules (Codex and Claude)
│   │   ├── corrections.py   Adopting a source's new value by hand
│   │   ├── views.py         Queries and summaries for the API and MCP
│   │   ├── api/             FastAPI: JSON API + the built frontend; server.py runs the dashboard server
│   │   ├── dashboard.py     The dashboard server in the background, on macOS, Linux and Windows
│   │   ├── sync/            Several machines: the cloud sign-in (config.py), snapshots (build, merge,
│   │   │                    restore), the sync client
│   │   ├── mcp_server.py    MCP tools
│   │   └── cli.py           Command line entry point
│   └── tests/
├── frontend/                Vite + React + TypeScript; every chart is hand-written SVG
│   └── src/
│       ├── api/             API types and requests
│       ├── panels/          The nine panels (00 Total … 08 Sources)
│       ├── cloud/           Cloud dashboard pages: Google and passkey sign-in, accounts, approving a computer
│       ├── components/      Panel frame, rail, top bar, toggles, pixel meters, tooltips, fun equivalents
│       ├── i18n/strings.ts  Every UI string (Chinese / English)
│       ├── theme/           Light and dark design tokens, theme switching
│       ├── styles/          Page frame (app.css), panel internals (panels.css), cloud pages (cloud.css)
│       ├── assets/bg.jpg    Background (made by scripts/grade-background.py)
│       └── lib/             Formatting, data shaping, sizing and motion, fun equivalents table (fun.ts)
├── cloud/                   The cloud ledger: a Cloudflare Worker (src/) and its D1 schema (migrations/)
├── scripts/                 Every build, test and run goes through these (.sh; .ps1 on Windows)
├── data/                    Ledger data (ignored by git)
├── docs/                    This file, README images and logo
└── research/                Data source investigation
```

## Data flow

```
CodexBar CLI ────────┐
Claude Code logs ────┤
Codex logs ──────────┤
Codex server ────────┼─> sources/*  ─> CaptureResult ─> ledger.apply_capture() ─> SQLite ─> views ─> API / MCP ─> frontend
CodexBar quota hist. ┘                     │                                        │  ▲
                                          └─> data/raw/ raw output archive          │  └─ sync: snapshot.apply() ← other machines' snapshots
Old CodexBar caches ─> backfill/ ─> ledger.apply_backfill()                         └─> sync: snapshot.build() → cloud ledger → other machines
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
| source | `codexbar`, `claude-logs`, `codex-logs`, `codex-server` |
| provider | `claude`, `codex`, `antigravity` |
| machine | The id of the machine whose logs these are; for account-wide data the account (`acct-<hash of the email>`; `account` in ledgers from before accounts were told apart) |
| scope | `machine` (local logs) or `account` (the whole account) |

Current sources:

| id | Reads | Notes |
|---|---|---|
| `codexbar` | `codexbar cost --provider <p> --days 365 --format json` for `claude` and `antigravity`. Runs on macOS, and on Linux when the CLI is installed | `CODEX_HOME` and `CLAUDE_CONFIG_DIR` are removed from the environment first: CodexBar keeps a single shared cache, and running its CLI against other folders rewrites the menu-bar app's cache. Fails when the ledger timezone differs from CodexBar's. Read Codex too before 2026-10-04, see below |
| `claude-logs` | Every Claude Code log on this computer (`homes.claude_log_roots`: `CLAUDE_CONFIG_DIR`, `~/.config/claude/projects`, `~/.claude/projects`, claude-swap profiles, the desktop app's agent sessions, WSL), read only | CodexBar's rules, below. On a Mac CodexBar's Claude figures are preferred (`views.MACHINE_SOURCE_ORDER`); this fills days CodexBar lacks |
| `codex-logs` | `sessions/**/*.jsonl[.zst]` and `archived_sessions/*.jsonl[.zst]` in every Codex home (`CODEX_HOME`, `~/.codex`, WSL), read only; prices from the catalog (`catalog.py`) | Each request is counted once, rules below. Newer Codex compresses logs untouched for a week into `.jsonl.zst`. The archived raw output is the per-day, per-model summary, which `accept` rebuilds rows from |
| `codex-server` | Starts the Codex CLI in `app-server` mode and sends `initialize`, then the read-only `account/read` and `account/usage/read`. The CLI is looked up in the ChatGPT app (`Contents/Resources/codex-cli/bin/codex`, earlier `Contents/Resources/codex`); on Windows the standalone or npm `codex.exe`; on Linux `~/.local/bin/codex`; then `codex` on PATH | Daily token totals only; the server's day boundary looks like UTC. The series is named after a hash of the account's email, so one account read on several machines is one series and several accounts add up. The first capture that knows the account folds the old `account` series into it (`repo.merge_series`). A machine where Codex never ran reports nothing instead of failing |
| `codexbar-quota` | `~/Library/Application Support/com.steipete.codexbar/history/*.json`, read only (macOS) | CodexBar compacts this history; the ledger keeps every sample it has seen |

## Codex logs (codex-logs)

Before 2026-10-04 Codex data for this Mac came from CodexBar. CodexBar counts the running total a forked conversation inherits as new usage on the day of the fork: in a forked log file, the first `token_count` event already carries the whole original conversation (on 4 October CodexBar reported 542M, the logs held 67.6M, and the server showed 13.3M while still catching up). The overcount on 14 September had the same cause. `sources/codex_logs.py` now reads the logs itself:

- Newer Codex versions write one `token_usage_record` per request, with a unique `response_id`. Files that contain such records use only them, deduplicated by `response_id` across all files, so history copied into another file is not counted again
- Older files without those records use `token_count` events: the difference between two consecutive running totals is one request. The first event in a file only counts its own `last_token_usage`, so an inherited running total is not counted. These events are deduplicated across files by timestamp and running total
- Each request goes to the day of its timestamp in the ledger timezone, is priced on its own, and is then summed per day and model; `total` is input + output, as in CodexBar
- The newer records also include requests such as context compaction, so the counts are slightly higher than CodexBar's (1% to 3% a day) and closer to the server's

The CodexBar Codex series is no longer captured; the ledger keeps its history (migration 2 settled its remaining open rows). The dashboard picks per day: `codex-logs` when the logs have that day, otherwise the CodexBar record from back then (`views.MACHINE_SOURCE_ORDER`).

## Claude Code logs (claude-logs)

The rules are CodexBar's (`CostUsageScanner+Claude.swift`, `CostUsagePricing.swift`), so a day counts the same on a Mac with CodexBar and on a machine without it. Checked against a Python port that reproduces CodexBar's own Claude tests, and on real logs (same tokens and cost to the micro-dollar):

- Files: every non-empty `*.jsonl` below each root, in sorted path order; dot-named entries below a root are skipped, symlinked folders are not followed; lines over 512 KiB are skipped. Subagent transcripts (`<session>/subagents/`) are included
- A request is an `assistant` entry with `message.model`, `message.usage` and a timestamp with a zone. Entries whose four token counts are all zero (`<synthetic>`, API errors) and Vertex AI entries are skipped
- Deduplication across all files and folders by `message.id` + `requestId` (without `requestId`: session id + `message.id`; without `message.id`: every entry counts). Within a file the last entry of a request wins; between files a complete entry beats a streaming placeholder, then a main session beats a sidechain, then a non-subagent file beats a subagent file, then the first path
- Model ids are normalized (`anthropic.`/Bedrock prefixes, `-vN:M`, a `-YYYYMMDD` date when the base is in the built-in table); that is also the model name stored
- Price per request: `claude-opus-4-6` and `claude-sonnet-4-6` always use built-in rates (with a long-context tier before 2026-03-13); otherwise models.dev's `anthropic` provider through CodexBar's id variants; then the built-in table; else unpriced. Missing cache rates fall back to the input rate; `context_over_200k` rates apply to the whole request above the tier size (input + cache reads + cache writes); 1-hour cache writes cost twice the input rate
- Per day and model the dollars are summed and rounded once. A model whose day has any unpriced request is unpriced for that day and left out of the day's cost

## Price catalog (catalog.py)

CodexBar keeps models.dev's catalog in `~/Library/Caches/CodexBar/model-pricing/models-dev-v1.json` (its Linux CLI under `~/.cache`). Where neither exists, TokenLedger builds the same file in `data/cache/`: fetched from https://models.dev/api.json when older than a day, projected to the fields CodexBar keeps, refused unless Anthropic and OpenAI both have priced models, and merged with the previous file so models models.dev drops keep their price. A failed fetch uses the old copy.

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
| `machines`, `meta` | Machine info (for another machine also which accounts it reads and when its data was taken and merged), schema version, this ledger's instance id, the time of the last snapshot it shared |

Migrations live in the `MIGRATIONS` list in `store/db.py`; they are only ever appended. Migration 3 adds what several machines need: `captures.machine` and `quota_samples.machine` (NULL for this machine's own), `sync_ref` on captures and revisions from another machine (so merging the same snapshot twice adds nothing), and the ledger's instance id.

## Settling rules

- Days are bucketed in the ledger timezone, which must match CodexBar's. On first use it is taken from CodexBar's setting, or from this machine when CodexBar has none (`data/settings.json`; on Windows through tzlocal and the tzdata package). A machine signing in to a cloud ledger with an empty ledger adopts that ledger's timezone
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

Pricing rules are in `pricing.py` and follow CodexBar v0.78.0. Codex: models.dev's `openai` entry for the normalised model, filled in from CodexBar's bundled OpenAI table; when a request's input, cached input included, passes the model's threshold, the whole request uses the long-context rates. The threshold is 272K for the models OpenAI prices that way (gpt-5.4/5.5, the gpt-5.6 and gpt-6 families) and 200K otherwise. Auto-review requests of ChatGPT-signed-in sessions are free from 2026-10-06. Claude: the same rules as CodexBar, including other vendors' models routed through Claude Code.

## Backfill from old caches

`tokenledger backfill` reads old CodexBar caches saved in `data/legacy/codexbar-cache-*/` (the originals are in `~/Library/Caches/CodexBar` and can be cleared at any time, so copy them into the project first). The rules and how they were checked are in `research/README.md`:

- Claude: a day's value = CodexBar's current value + requests that exist only in the old caches (deduplicated by `messageId + requestId`). v4/v5 requests carry CodexBar's cost from back then; v2 has no cost and is recomputed using the share of 1-hour cache writes worked out from requests v2 and v4 have in common
- Codex: for days CodexBar no longer reports, take the per-day total of the newest old cache covering that day (v10 over v9; v9 also counted history copied by forked sessions); cost is recomputed per request with the long-context rule and scaled to the total

## Manual corrections

`tokenledger accept <revision_id>` (or MCP `accept_revision`) re-parses the full row from the raw archive behind that revision and writes it into the ledger; the old value is kept as `replaced`. `missing` revisions cannot be accepted: the ledger does not delete history.

## Dashboard rules (views.py)

- Claude: the local data of every machine, added up (CodexBar where it runs, `claude-logs` elsewhere)
- Codex: account-wide tokens on days that have them (every account added up), local data otherwise. A machine's own logs count through the account it reads; the logs of a machine that reads no account are added on top (`_coverage`). The server's data is complete only half a day to a day later, so open days take the larger of the two. Final days use account data only, since the server's day boundary is UTC and mixing it with local data would count usage around midnight twice. Cost only comes from local data; `cost_tokens` tells how many tokens the cost covers
- When one machine has several local sources for a provider, each day takes the first source in `MACHINE_SOURCE_ORDER` (`codex-logs`, `codexbar`, `claude-logs`) that has the day; sources that are no longer captured (`RETIRED_SOURCES`) do not appear in the source health list
- Machines (`machine_usage`): each machine's local data per day, and the account usage none of them logged ("Elsewhere")
- Source health is per machine and source. A source whose last attempt is more than a day older than that machine's newest capture (no longer run there) is left out
- Quota peaks: weekly windows are grouped by reset time and take the highest value, skipping windows that stayed at 0% (Codex's weekly window rolls, and when idle every sample says "resets in 7 days"); 5-hour windows take the highest of each local day

## HTTP API

Listens on 127.0.0.1 only, and refuses requests whose Host is not a loopback name (DNS rebinding) and changing requests from another origin. Everything except `POST /api/capture` is read-only.

| Path | Returns |
|---|---|
| `GET /api/session` | `{"mode": "local"}`; the cloud dashboard answers with the signed-in person |
| `GET /api/view?range` | Everything one dashboard range (`d30`, `d90`, `d180`, `y1`, `all`) shows: status, summary, daily, models, machines, quota. The dashboard reads only this; the cloud serves the same shape |
| `GET /api/status` | Machine, timezone, ledger row counts, each source's latest attempt per machine (`sources`: machine, ok, time, error, last success), every machine in the ledger (`machines`), and whether this machine is signed in to a cloud ledger with its last sync (`sync`) |
| `GET /api/summary?start&end` | Totals per provider, cost coverage, token makeup, peak day, streaks |
| `GET /api/daily?start&end` | Tokens, cost, quality and open flag per day and provider |
| `GET /api/models?start&end` | Totals per model (local data) |
| `GET /api/quota?start&end&max_points` | Raw quota samples (thinned) |
| `GET /api/quota/peaks?start&end` | Quota peaks |
| `GET /api/quota/now` | Latest reading and reset time of every quota window |
| `GET /api/revisions?limit`, `GET /api/captures?limit` | Revisions and captures |
| `POST /api/capture` | Capture now (then sync, when signed in to a cloud ledger) |

Interactive docs at `/api/docs`.

## Dashboard

Console style: square corners everywhere, large areas only black, white and grey; Claude is orange and Codex blue, and orange is also the only accent. Titles and numbers use Chakra Petch, small marks such as panel numbers and code tags use the pixel font Silkscreen, and data detail uses JetBrains Mono; all three fonts are bundled, so the page works offline. Each panel is a thin frosted-glass sheet with grain and an edge light that follows the pointer, over a colour-graded photo that the CSS darkens or lightens per theme.

There is no chart library; every chart is SVG drawn by React: stacked daily token bars, the weekly cost block matrix, the heatmap, monthly LED bars and segmented quota bars. Charts that fill their panel read the real size of their area with `useSize` before drawing, so panels of different heights in one row line up and everything reflows when the window changes size.

The interface follows the browser's language (Chinese or English) until you pick one, and the system theme until you pick light or dark; both choices are stored in the browser.

`index.html` is served with `Cache-Control: no-cache`, so a rebuild shows up on refresh; hashed asset files are cached as usual.

The cloud dashboard is the same build. It asks `/api/session` first: signed out, it shows the Google or passkey sign-in; on `/setup`, `/join` and `/recover` it creates an account or adds a way to sign in; on `/link` it approves a computer. The data comes from `/api/view` as on a local dashboard, published there by the machines.

## Cloud ledger (cloud/)

A Cloudflare Worker with a D1 database, deployed with `scripts/cloud.sh` (wrangler). It stores and hands out what machines share and never reads it, so it stays far inside the free plan's limits (10 ms CPU per request):

- `src/google.ts`: signing in with Google (OpenID Connect, ID token posted back, checked against Google's keys; no client secret); only linked Google accounts
- `src/auth.ts`: people. Passkeys (SimpleWebAuthn), 30-day sessions in an `HttpOnly`, `Secure`, `SameSite=Strict` cookie, one-time codes for the first account, invitations and recovery
- `src/sync.ts`: machines. Signing in (`/api/link/start`, approve on the web, `/api/link/poll` hands the device token over once); the snapshot list, each machine's snapshot, the dashboard per range (`/api/sync/*`, bearer token, checked against the machine id, ledger instance and computer); devices seen by their owner; `/api/view` for the signed-in person
- `src/index.ts`: routing, the static files, security headers on every response, and refusing changing cookie requests from another origin
- `migrations/`: the D1 schema. Secrets are stored as SHA-256 hashes; bytes are split into parts below D1's row size limit

## Server lifecycle

`api/server.py` runs the dashboard server with a controlled lifecycle; `dashboard.py` manages it in the background. Process checks use psutil, so this works the same on macOS, Linux and Windows; on Windows, where there is no SIGTERM for another process, `stop` posts to a loopback-only shutdown path with a random token from the state file.

- `dashboard start` launches the server in its own session (`start_new_session`), detached from the terminal that started it, and returns once `/api/status` answers. Only one runs at a time; a port taken by another program is an error
- On start the server writes `.build/run/dashboard.json` (pid, port, start time, shutdown token) and removes it on exit. The management commands find the server through it and first check that the pid really is a TokenLedger server before sending signals; a state file left by a crash is detected and removed
- It runs until it is told to stop. Every exit is graceful: SIGTERM (`dashboard stop`), Ctrl+C, SIGHUP (its terminal closed). uvicorn re-raises SIGINT/SIGTERM after handling them, so the server installs Python-level handlers first to make sure cleanup and logging finish. `stop` force-kills only after 10 seconds
- When the machine is signed in to a cloud ledger, a background thread syncs every 10 minutes (skipping a round while another process syncs)
- The log is `.build/run/dashboard.log`, overwritten on every start, so only the latest run is kept

## MCP tools

`scripts/mcp.sh` serves over stdio: `ledger_status`, `usage_summary`, `daily_usage`, `model_breakdown`, `quota_history`, `quota_peaks`, `quota_now`, `list_revisions`, `list_captures`, `dashboard_status` (read only), `machine_usage` (read only), `run_capture`, `accept_revision`, `sync_now` (write to the ledger, or sync with the cloud ledger), and `dashboard_start`, `dashboard_stop` (control the dashboard server).

## Several machines

See [MULTI_MACHINE.md](MULTI_MACHINE.md): how a sync works, merging rules, signing in and security, platforms, known gaps.

## Running cost and output

See "Where things live" in the README.
