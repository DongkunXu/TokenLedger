# Several machines, one ledger

TokenLedger can count usage from several computers (macOS, Windows, Linux) in one ledger. Every machine captures into its own ledger as before, and shares it through a **cloud ledger**: a small Cloudflare Worker with a D1 database on your own Cloudflare account and domain. Every machine merges what the others share, so each machine's dashboard shows all of them, and the cloud ledger serves the same dashboard on the web, behind a passkey sign-in.

Without sync set up, a machine works on its own, exactly as before.

## Why every machine has to capture

| Usage | Where it is recorded | What TokenLedger does |
|---|---|---|
| Claude Code on a computer (terminal, IDE, desktop app) | That computer's `~/.claude/projects` only. Anthropic does not sync it to other devices, and Pro/Max plans have no usage API | Each machine reads its own logs; the ledger adds machines up |
| Claude Code Remote Control (steering a session on computer A from a phone, browser or computer B) | Computer A only; B is a window into A's session and writes nothing locally | Counted once, on A |
| Claude Code cloud sessions (claude.ai/code, the Code tab in the app, `claude --cloud`, routines) | Anthropic's cloud VM; no computer has a log | **Not counted.** Nothing local to read |
| A cloud session pulled down with `claude --teleport` | The computer that pulled it gets a full copy of the conversation, including the cloud part | Counted on that computer. Requests are deduplicated by id, so pulling the same session twice on one computer counts it once |
| Codex CLI, IDE extension and desktop app | That computer's `~/.codex` only (OpenAI does not sync local threads either) | Each machine reads its own logs for tokens per model and cost |
| Codex account totals | OpenAI's servers, for all devices of one account | Read on any machine signed in to the account (`codex-server`); each account is counted once, several accounts are added up |
| Codex remote connections / handoff | Runs on the host it is connected to; a handoff moves the thread and its log to the other host | Counted where the log is. If both hosts keep a copy, see "Known gaps" |

## How it works

```
Mac                          Windows PC                   Linux box
capture → own ledger         capture → own ledger         capture → own ledger
   │ share snapshot ▲ merge     │ share ▲ merge               │ share ▲ merge
   └────────────────┴──── https ┴───────┴─────────────────────┴───────┘
                         cloud ledger (Cloudflare Worker + D1)
                         stores one snapshot per machine and the latest dashboard;
                         serves the dashboard to you after a passkey sign-in
```

One sync (after every capture, every 10 minutes while the dashboard server runs, and with `tokenledger sync now`):

1. Read the list of snapshots in the cloud: one per machine, with the time it was taken.
2. If the cloud holds a newer snapshot of **this** machine than this ledger last shared, this ledger is behind (its data folder was restored from a backup or recreated). Settled days come back from that snapshot first.
3. Merge every other machine's snapshot that is newer than the one merged last.
4. Share this machine's own snapshot.
5. Publish the dashboard: every range, computed on this machine from the merged ledger.

The cloud never computes anything and never reads the data; it stores and hands out compressed bytes. A failed sync loses nothing: every ledger keeps what it has, and the next sync moves whole snapshots again.

### What a snapshot holds

Everything one machine is responsible for: every row of its own series, every row of the account series it reads itself (Codex server totals), their revisions, its latest 300 captures (for source health) and its own quota samples of the last 400 days. A ledger is a few thousand rows a year, so this stays small (about 200 KB compressed for a year of a busy Mac), and a lost or repeated upload can never leave a gap or count anything twice.

### Merging rules

- **Each machine writes only its own series.** A series is keyed by machine id, so two machines never write the same rows. Rows of another machine are mirrored as that machine has them: added or updated, never deleted.
- **Final rows keep their value.** When another machine shares a different value for a row that is final here (its ledger was restored from an older backup, say), the value here stays and the other one is recorded as a revision, once. A deliberate correction on that machine (`accept`, a backfill) is taken over.
- **Account series** can come from several machines signed in to the same account. A final row keeps its value (a different final value is recorded as a revision); an open row takes an incoming row that is final or newer.
- **Accounts and machines add up without counting twice.** Each machine records which account series it reads. A machine's own Codex logs belong to the account it reads, so they count through that account's total. The logs of a machine that reads no account (Codex signed in with an API key, say) are added on top. The Machines panel splits the tokens by machine and shows the account usage no machine logged (other devices, cloud tasks) as "Elsewhere".
- **Old snapshots are ignored.** Each ledger remembers the time of the last snapshot it merged from each machine.
- **Days must line up.** All machines of a ledger use the same timezone and grace period; the first machine sets them, and a machine that signs in with an empty ledger adopts them. A snapshot with other settings is refused.
- **Account names.** A Codex account is identified by a hash of its ChatGPT account id (the email's hash when no id is returned), never the id or email themselves. When the id shows up for an account known so far by its email, the two series are folded together on every machine.

## Signing in and security

- **People** sign in to the web dashboard with a **passkey** (Touch ID, Face ID, Windows Hello, a phone or a security key). There are no passwords to guess or leak. The session is an `HttpOnly`, `Secure`, `SameSite=Strict` cookie that lasts 30 days; requests that change something must come from the dashboard's own origin.
- **Machines** sign in with `tokenledger sync login <url>`: the machine shows a code and opens the browser, you check the code and approve it while signed in, and the machine receives **its own device token**. There is no shared key to copy between machines. The token is stored in `data/sync.json`, readable by your user only.
- A device token works only for its machine id, its ledger and its computer (a hash of the computer's hardware or installation id). A copied data folder is refused on the other computer, and a recreated ledger has to sign in again.
- Remove a machine in the account menu of the web dashboard; its token stops working at once. `tokenledger sync logout` does the same from the machine.
- Each person has their own ledger. An admin (the first account) can invite others with a one-time link; neither sees the other's data. Whoever runs the deployment can technically read the database, so friends may prefer to deploy their own.
- The cloud ledger stores only numbers: token counts, costs, model names, dates, source health and machine names. No prompts, file names or project paths.
- Secrets are stored hashed (session cookies, device tokens, one-time codes). Every response carries a strict Content-Security-Policy, HSTS and no framing.
- The deployment's own details (domain, database id) live in `data/cloud.json`, outside git; the Cloudflare sign-in of the deploy tool in `.build/wrangler/`.

## Setting it up

### Once: deploy the cloud ledger

You need a Cloudflare account with a domain on it (the free plan is enough).

```bash
scripts/cloud.sh login                       # sign in to Cloudflare in the browser (deploy rights only)
scripts/cloud.sh deploy tokens.example.com   # creates the database, deploys, prints a setup link
```

Open the setup link, pick a name and create a passkey. Later updates: `scripts/cloud.sh deploy`.

### On every machine

```bash
scripts/tokenledger.sh sync login https://tokens.example.com   # approve it in the browser
scripts/install-agent.sh                                       # capture (and sync) at 09:00 and 21:00
# Windows: .\scripts\tokenledger.ps1 sync login https://tokens.example.com; .\scripts\install-agent.ps1
```

`tokenledger sync status` shows the outcome of the last sync, `tokenledger sync now` syncs right away, `tokenledger sync logout` signs the machine out (its ledger stays).

### Lost every passkey

Whoever runs the deployment makes a recovery link: `scripts/cloud.sh recover <name>`. It lets that account add a new passkey, once, within 24 hours.

## Platforms

| | macOS | Windows | Linux |
|---|---|---|---|
| Claude | CodexBar (`codexbar`) | `claude-logs` | `claude-logs` (CodexBar's CLI instead when installed) |
| Codex | `codex-logs` + `codex-server` | same | same |
| Antigravity | CodexBar | not yet | not yet (CodexBar's CLI when installed) |
| Quota history | CodexBar | no | no |
| Price catalog | CodexBar's | TokenLedger's own copy of models.dev, built like CodexBar's | same (or CodexBar's `~/.cache` copy) |
| Scheduled capture | launchd (`install-agent.sh`) | Task Scheduler (`install-agent.ps1`) | systemd user timer (`install-agent.sh`) |

`claude-logs` follows the rules of CodexBar v0.78.0 (which files, which entries, deduplication by message and request id, which copy wins, model normalization, built-in and models.dev prices including other vendors' models, rounding), so a day counts the same on every machine. Checked against a Python port of CodexBar's own tests and, on a Mac, day by day against CodexBar itself.

Where logs are read on each machine (`homes.py`), every place once:

- Claude: `CLAUDE_CONFIG_DIR` when set (then only there, as CodexBar does); otherwise `~/.config/claude/projects`, `~/.claude/projects`, claude-swap profiles and the desktop app's agent sessions; on Windows also every WSL distribution's homes (`\\wsl.localhost\<distro>\home\*\.claude`). Reading a WSL distribution that is not running starts it; set `"wsl": "running"` or `"off"` in `data/settings.json` to change that. Do not install TokenLedger inside WSL as well: the Windows side already reads it.
- Codex: `CODEX_HOME`, `~/.codex`, WSL homes; plain `.jsonl` and compressed `.jsonl.zst` rollouts (newer Codex compresses logs untouched for a week).

## Decisions

- **The cloud stores, machines compute.** Every machine merges the snapshots itself and computes the dashboard; the cloud only keeps bytes. One implementation of the ledger rules, and the Worker stays far inside the free plan's limits.
- **Every machine holds every machine's data.** Each machine's dashboard and MCP server see the whole ledger, and each machine's own snapshot in the cloud doubles as a backup of its settled days.
- **Day-level sync, not request-level.** Requests are deduplicated by id within each machine. Across machines, the same request is only seen twice in rare cases (below); syncing every request would make the data much larger.

## Known gaps

- **Claude cloud sessions** are not counted (no local log anywhere).
- **The same conversation on two machines**: a session teleported onto two different computers, a Codex handoff where both hosts keep the log, or a `~/.claude` folder synced between computers by another tool. Each machine counts it, so the ledger counts it twice. Deduplicating across machines would need request ids in the snapshots.
- **A machine that stops syncing** keeps its last open days open in the other ledgers until it syncs again.
- **Not yet tested on real hardware**: Windows (paths, Task Scheduler, WSL).
