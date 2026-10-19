<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/logo-dark.svg">
    <img src="docs/images/logo.svg" width="72" alt="TokenLedger logo">
  </picture>
</p>

<h1 align="center">TokenLedger</h1>

<p align="center">
  An append-only ledger of your AI coding usage: Claude, Codex and Antigravity tokens and their API list-price cost, kept on your Mac for good.
</p>

<p align="center">
  <b>English</b> · <a href="README.zh-CN.md">简体中文</a>
</p>

![TokenLedger dashboard](docs/images/dashboard-en.jpg)

## Why

Claude Code deletes its session logs after 30 days, Codex logs get cleaned up, and tools that read them can change how they count. TokenLedger copies the daily numbers into a local SQLite ledger. Each day stays open for three days, then it is final: whatever happens to the logs later, the history stays as it was. When a source later reports something different for a final day, the ledger records it as a revision and keeps its own value.

It comes with a local web dashboard and an MCP server, so you can also ask Claude about your usage.

## Features

- **Daily tokens and cost** per provider and per model, with cache reads, cache writes, input and output kept apart
- **Account-wide Codex totals** from OpenAI's servers (all devices), next to the per-request detail from this Mac's logs
- **Quota history**: 5-hour and weekly limit usage for Claude and Codex, with weekly peaks
- **A dashboard** with daily and monthly charts, an activity heatmap, weekly cost, model breakdown and source health, in English and Chinese, light and dark
- **Fun equivalents**: your tokens as copies of *Dream of the Red Chamber*, laps around the Earth, keyboards worn out, and more

![Fun equivalents](docs/images/equivalents-en.jpg)

## Requirements

| What | Why | Install |
|---|---|---|
| macOS | CodexBar and the data paths are macOS ones | |
| [CodexBar](https://github.com/steipete/CodexBar) | Claude and Antigravity usage, the model price table, quota history. The app also installs the `codexbar` command line tool. Open it once so it fills its price cache | `brew install --cask codexbar` |
| Codex (optional) | Codex usage. Logs are read from `~/.codex`; account totals come from the Codex CLI bundled with the ChatGPT app, or `codex` on your PATH | ChatGPT app, or `npm i -g @openai/codex` |
| Python 3.13+ | Backend | `brew install python@3.13` |
| uv | Python environment | `brew install uv` |
| Node.js | Builds the dashboard | `brew install node` |

Everything runs locally. The only network request is the Codex CLI asking OpenAI for your account's daily totals, the same request the Codex app makes.

## Quick start

```bash
git clone https://github.com/DongkunXu/TokenLedger.git
cd TokenLedger
scripts/tokenledger.sh capture
scripts/dashboard.sh open
```

The first capture creates `data/` with the ledger and your settings, and the first `open` builds the dashboard and opens http://127.0.0.1:8765/. Later, press **Capture** on the dashboard or run `scripts/tokenledger.sh capture` again.

### How often to capture

Accuracy does not depend on how often you capture, as long as the logs are still there when you do. Once every week or two is enough. Capture before you delete Claude or Codex conversations, and keep in mind that Claude Code removes session logs after 30 days.

### Use it from Claude (MCP)

```bash
claude mcp add tokenledger --scope user -- "$PWD/scripts/mcp.sh"
```

Then ask things like "how many tokens did I use last week", "capture now" or "open the dashboard".

## The dashboard server

| Command | What it does |
|---|---|
| `scripts/dashboard.sh start` | Start in the background (does nothing when already running) |
| `scripts/dashboard.sh open` | Start if needed and open it in the browser |
| `scripts/dashboard.sh stop` | Stop |
| `scripts/dashboard.sh restart` | Restart; needed after backend code changes |
| `scripts/dashboard.sh status` | Whether it runs, its address and uptime |
| `scripts/dashboard.sh logs` | Log of the current (or last) run |
| `scripts/dashboard.sh run` | Run in this terminal instead; Ctrl+C stops it |

The server is detached from the terminal that started it. It stops on `stop` or `restart`, on Ctrl+C in `run` mode, at logout, and after 12 hours without any page or API request (`--idle-hours`, 0 keeps it running). Each reason is written to the log. It listens on 127.0.0.1 only.

The header shows when every source last succeeded. When a source fails, the header shows a red notice until that source succeeds again, and the Sources panel shows the error.

## Other commands

| Command | What it does |
|---|---|
| `scripts/tokenledger.sh capture` | Capture now |
| `scripts/tokenledger.sh status` | Ledger overview, recent captures and failures |
| `scripts/tokenledger.sh accept <id>` | Adopt a source's new value for a final day (see below) |
| `scripts/tokenledger.sh backfill --dry-run` | Preview a backfill from old CodexBar caches copied into `data/legacy/` |
| `scripts/test.sh` | Backend tests and frontend type check |
| `scripts/build.sh` | Build the dashboard |
| `scripts/clean.sh` | Remove build output and dependencies; ledger data stays |
| `scripts/install-agent.sh` | Scheduled capture at 09:00 and 21:00 via launchd (blocked by macOS when the project lives in `~/Documents`) |
| `scripts/uninstall-agent.sh` | Remove the scheduled capture |
| `scripts/screenshots.py`, `scripts/grade-background.py` | Retake the README screenshots, regrade the background photo (usage at the top of each file) |

## Data sources

| Source | Provides | Scope |
|---|---|---|
| `codexbar cost` | Claude and Antigravity tokens and list-price cost per day and model | This Mac's logs |
| Codex logs (`~/.codex/sessions`) | Codex tokens and list-price cost per day and model, each request counted once | This Mac |
| Codex server (`account/usage/read` via `codex app-server`) | Daily token totals | Your whole OpenAI account |
| CodexBar quota history | 5-hour and weekly limit percentages | Your accounts |

For Codex, the dashboard prefers the account-wide totals. The server lags half a day to a day, so for days still open it takes whichever of the server and this Mac is larger. Cost only comes from this Mac's per-request detail; the Sources panel shows how much of the tokens it covers.

## Settling rules

- Day D is *open* on D, D+1 and D+2; every capture overwrites it
- The first capture after that settles it: if the source's total did not shrink, the ledger takes the source's numbers and freezes the day; if it shrank or the day is gone, the ledger keeps its own numbers and records a revision
- After that the numbers are fixed; a different value from a source is only recorded as a revision
- If a revision turns out to be a real correction by the source, `accept` adopts it and keeps the old value on record

## Settings

`data/settings.json` is created on first use:

| Key | Default |
|---|---|
| `timezone` | CodexBar's day timezone if set, else this Mac's timezone. Days are bucketed in it, and it must match CodexBar's |
| `grace_days` | 3 |
| `machine` | An id and label for this Mac |

Changing the timezone after days have been settled makes old and new days disagree at midnight, so set it before your first capture if the default is wrong.

## Where things live

| What | Where | Cleaned by |
|---|---|---|
| Ledger, raw capture archive, settings | `data/` | You |
| Python env, uv/npm caches, temp files | `backend/.venv`, `.build/` | `scripts/clean.sh` |
| Frontend dependencies and build | `frontend/node_modules`, `frontend/dist` | `scripts/clean.sh` |
| Dashboard state and log (last run only) | `.build/run/` | Removed on exit; `scripts/clean.sh` |
| Scheduled capture | `~/Library/LaunchAgents/io.github.dongkunxu.tokenledger.capture.plist` | `scripts/uninstall-agent.sh` |

A capture takes about 2 seconds and archives about 16 KB of raw output.

```
backend/    Python: ledger, sources, API, MCP, command line
frontend/   React dashboard (charts are hand-written SVG)
scripts/    Build, run and test scripts (caches stay inside the project)
data/       Your ledger (not in git)
docs/       Architecture notes and images
research/   The data source investigation the design is based on
```

Architecture, data model and API: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Credits

- [CodexBar](https://github.com/steipete/CodexBar) by Peter Steinberger, which does the hard work of reading Claude's logs and pricing them
- Fonts: [Chakra Petch](https://fonts.google.com/specimen/Chakra+Petch), [JetBrains Mono](https://www.jetbrains.com/lp/mono/) and [Silkscreen](https://fonts.google.com/specimen/Silkscreen), via Fontsource (SIL Open Font License)
- Background: a colour-graded photo by Mohammad Alizade on [Unsplash](https://unsplash.com/photos/XgeZu2jBaVI)

Not affiliated with Anthropic, OpenAI or Google. Costs are estimates at API list prices; subscription plans are billed differently.

## License

[MIT](LICENSE)
