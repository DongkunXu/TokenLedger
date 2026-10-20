# Several machines, one ledger

TokenLedger can count usage from several computers (macOS, Windows, Linux) in one ledger. One machine is the **hub**: it keeps the combined ledger and its dashboard shows everything. Every other machine is a **node**: it captures into its own ledger as before, then pushes it to the hub.

Without sync set up, a machine works on its own, exactly as before.

## Why every machine has to capture

| Usage | Where it is recorded | What TokenLedger does |
|---|---|---|
| Claude Code on a computer (terminal, IDE, desktop app) | That computer's `~/.claude/projects` only. Anthropic does not sync it to other devices, and Pro/Max plans have no usage API | Each machine reads its own logs; the hub adds machines up |
| Claude Code Remote Control (steering a session on computer A from a phone, browser or computer B) | Computer A only; B is a window into A's session and writes nothing locally | Counted once, on A |
| Claude Code cloud sessions (claude.ai/code, the Code tab in the app, `claude --cloud`, routines) | Anthropic's cloud VM; no computer has a log | **Not counted.** Nothing local to read |
| A cloud session pulled down with `claude --teleport` | The computer that pulled it gets a full copy of the conversation, including the cloud part | Counted on that computer. Requests are deduplicated by id, so pulling the same session twice on one computer counts it once |
| Codex CLI, IDE extension and desktop app | That computer's `~/.codex` only (OpenAI does not sync local threads either) | Each machine reads its own logs for tokens per model and cost |
| Codex account totals | OpenAI's servers, for all devices of one account | Read on any machine signed in to the account (`codex-server`); each account is counted once, several accounts are added up |
| Codex remote connections / handoff | Runs on the host it is connected to; a handoff moves the thread and its log to the other host | Counted where the log is. If both hosts keep a copy and both are nodes, see "Known gaps" |

## How it works

```
node (Windows PC)            node (Linux box)              hub (Mac or server)
capture → own ledger         capture → own ledger          capture → own ledger
        │ push snapshot              │ push snapshot              ▲
        └──────────── LAN (or Tailscale / tunnel) ────────────────┘
                                                            dashboard shows all machines
```

- **A push is a full snapshot**, not a list of changes: every ledger row the node is responsible for (its own series and the account series it read), their revisions, its latest 300 captures (for source health) and its quota samples. A ledger is a few thousand rows a year, so this stays small (tens to hundreds of KB, compressed), and a lost or repeated push can never leave a gap or count anything twice: the next push repairs everything.
- **Each machine writes only its own series.** A series is keyed by machine id, so two machines never write the same rows. The hub mirrors a node's rows as the node has them: added or updated, never deleted. The node has already applied the ledger's settling rules.
- **Account series** (Codex server totals) can come from several machines signed in to the same account. On the hub a final row stays as it is, and an open row takes an incoming row that is final or newer.
- **Old snapshots are ignored.** The hub remembers the time of the last snapshot it applied from each machine; an older one (a delayed request) changes nothing.
- **Days must line up.** A snapshot bucketed in another timezone is refused. A machine that joins with an empty ledger adopts the hub's timezone automatically.
- **When pushes happen:** after every capture on a node (command line, dashboard, MCP, scheduled), and with `tokenledger sync push`. A failed push is recorded and shown on the node's dashboard; the data stays in the node's ledger and goes with the next push.

## Finding the hub

The hub answers UDP broadcasts on the sync port (8766 by default) in its local network. A node broadcasts a question with a random nonce; the hub answers with its machine id, name, port and a proof (an HMAC of the nonce made with the ledger key). A node accepts only an answer whose proof matches its key and whose machine id is the hub it joined, so another TokenLedger hub on the same network (a school or office network can have many) is never used.

The node remembers where the hub answered last time and tries that first. If the hub's address changed (DHCP), the node finds it again by broadcast.

Broadcasts stay inside one subnet. For anything else, give the node a fixed address with `--hub`:

- **Tailscale**: `--hub http://<hub-name>:8766` (Tailscale's 100.64.0.0/10 addresses are accepted by default)
- **Cloudflare Tunnel** on your own domain: point the tunnel at `http://127.0.0.1:8766` on the hub and use `--hub https://ledger.example.com`
- **A server on your LAN** as the hub: run the hub there; nodes find it by broadcast or use its fixed address

`--network 192.168.1.0/24` (repeatable) limits both sides to the given networks: the hub ignores requests from elsewhere, and a node broadcasts only there. Without it, the hub accepts private addresses (10/8, 172.16/12, 192.168/16, 100.64/10, link-local, loopback) only.

## Security

- `tokenledger sync hub-init` makes a random 160-bit **ledger key** (`tlk1-XXXX-...`). Every machine of the ledger has it; it never travels over the network.
- Every request and response body is gzip-compressed JSON sealed with **AES-256-GCM** under a key derived from the ledger key. The associated data binds each message to its direction, path, sending machine, time and request id. Without the key nobody can read a push, forge one, or fake the hub's acknowledgement (which would make a node believe its data had arrived).
- Requests more than 10 minutes off the hub's clock are refused; the error says so.
- `sync.json` (with the key) is written readable by its owner only. Revoking a machine means making a new key (`sync hub-init` after `sync leave` on the hub) and re-joining the others.
- Only numbers leave a machine: token counts, costs, model names, dates, source health and machine names. No prompts, file names or project paths. Codex accounts are identified by a hash of the email, never the email.
- Each person (or team) runs their own hub; nobody else holds the data. There is no shared service.

## Setting it up

On the machine that will be the hub (always-on is best: a Mac that stays on, or a server):

```bash
scripts/tokenledger.sh sync hub-init          # prints the ledger key
scripts/install-hub-agent.sh                  # macOS/Linux: start with the machine, restart if it stops
# or: scripts/hub.sh start                    # just for now
# Windows: .\scripts\tokenledger.ps1 sync hub-init; .\scripts\install-hub-task.ps1 -Firewall (elevated)
```

On every other machine:

```bash
scripts/tokenledger.sh sync discover          # lists hubs on this network (optional)
scripts/tokenledger.sh sync join --key tlk1-....          # finds the hub, checks the key, first push
scripts/install-agent.sh                      # capture (and push) at 09:00 and 21:00
# Windows: .\scripts\tokenledger.ps1 sync join --key tlk1-...; .\scripts\install-agent.ps1
```

`tokenledger sync status` shows the role and the last push. `tokenledger sync leave` makes a machine standalone again (its ledger stays).

## Moving the hub to another machine

1. Stop the old hub (`scripts/uninstall-hub-agent.sh` or `scripts/hub.sh stop`).
2. Copy the old hub's `data/ledger.sqlite` to the new hub's `data/` (it holds every machine's history, including machines that are gone). Do not copy `settings.json`: the new hub keeps its own machine id.
3. On the new hub: `sync hub-init --key <the same key>`, then start it.
4. On every node, `sync leave` and `sync join` again. Each node sends its full snapshot, which changes nothing that is already there.

## Platforms

| | macOS | Windows | Linux |
|---|---|---|---|
| Claude | CodexBar (`codexbar`), with `claude-logs` filling days CodexBar lacks | `claude-logs` | `claude-logs` (CodexBar's CLI too when installed) |
| Codex | `codex-logs` + `codex-server` | same | same |
| Antigravity | CodexBar | not yet | not yet (CodexBar's CLI when installed) |
| Quota history | CodexBar | no | no |
| Price catalog | CodexBar's | TokenLedger's own copy of models.dev, built like CodexBar's | same (or CodexBar's `~/.cache` copy) |
| Scheduled capture | launchd (`install-agent.sh`) | Task Scheduler (`install-agent.ps1`) | systemd user timer (`install-agent.sh`) |
| Hub service | launchd (`install-hub-agent.sh`) | Task Scheduler (`install-hub-task.ps1`) | systemd user service (`install-hub-agent.sh`) |

`claude-logs` follows CodexBar's rules exactly (which files, which entries, deduplication by message and request id, last chunk wins, model normalization, built-in and models.dev prices, rounding), so a day counts the same on every machine. Checked against a Python port of CodexBar's own tests and on real logs.

Where logs are read on each machine (`homes.py`), every place once:

- Claude: `CLAUDE_CONFIG_DIR`, `~/.config/claude/projects`, `~/.claude/projects`, claude-swap profiles, the desktop app's agent sessions; on Windows also every WSL distribution's homes (`\\wsl.localhost\<distro>\home\*\.claude`). Reading a WSL distribution that is not running starts it; set `"wsl": "running"` or `"off"` in `data/settings.json` to change that. Do not install TokenLedger inside WSL as well: the Windows side already reads it.
- Codex: `CODEX_HOME`, `~/.codex`, WSL homes; plain `.jsonl` and compressed `.jsonl.zst` rollouts (newer Codex compresses logs untouched for a week).

## Decisions

- **One hub, push only.** The hub is the only place with the combined ledger. Nodes do not pull other machines' data; their own dashboards show their own machine.
- **Accounts are internal only.** The dashboard shows totals. Codex account series are keyed by a hash of the email only so that different accounts add up and one account read on several machines is counted once. (The workspace id is not part of the key: it is not always returned, and a key that changes between captures would count an account twice.)
- **Day-level sync, not request-level.** Requests are deduplicated by id within each machine. Across machines, the same request is only seen twice in rare cases (below); syncing every request would make the hub's data and the protocol much larger.

## Known gaps

- **Claude cloud sessions** are not counted (no local log anywhere).
- **The same conversation on two nodes**: a session teleported onto two different computers, a Codex handoff where both hosts keep the log, or a `~/.claude` folder synced between computers by another tool. Each machine counts it, so the hub counts it twice. Deduplicating across machines would need request ids on the hub.
- **Tested here**: the sync protocol, discovery (over loopback) and pushes between two ledgers over HTTP; the Claude and Codex readers on Linux. **Not yet tested on real hardware**: Windows (paths, Task Scheduler, WSL), and broadcast discovery on a real LAN (the development sandbox blocks broadcasts).
- **Remote dashboard**: the dashboard is still local to each machine. A read-only dashboard on your own domain (Cloudflare) can be added on top of the hub later.
