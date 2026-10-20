# TokenLedger data source investigation (2026-09-27)

What was found about the data sources before the tool was written. The conclusions are built into `backend/`; see `docs/ARCHITECTURE.md`.

## Raw data

The raw exports this was based on are kept locally in `snapshots-2026-09-27/` and are not part of the repository: the Codex server's daily account usage from 2026-02-26 to 2026-09-26 (185 days, 5,232,931,596 tokens) and that day's `codexbar cost --days 365 --format json` export for Claude and Codex (CodexBar 0.60.4).

## Sources

| Source | Scope | Detail | Reliability |
|---|---|---|---|
| CodexBar export (local logs) | This machine only, Claude + Codex | Per day: each token type, tokens and cost per model | Gone once the logs are deleted; past days can also change (9/14 went from 51.6M to 66.9M within a day) |
| Codex server `account/usage/read` | Whole account, all devices | One token total per day, no types, no cost | About 5% off the local totals for complete days; day boundary looks like UTC |
| Codex server per-thread query (with `threadId`) | — | — | Returns null for this account, unusable |
| Old CodexBar caches `~/Library/Caches/CodexBar/cost-usage/*-v*.json` | Each version's scan window | One row per request | Had a double-counting bug; deduplicate yourself, see below |
| `threads.tokens_used` in Codex's `state_5.sqlite` | One running total per conversation | Very coarse | Resumed conversations count again and forks double count; not used |
| CodexBar quota percentage history `history/*.json` | Whole account, from 9 April | Percentages only | Compacted and capped |

## The two providers count differently

- Claude's `inputTokens` **excludes** cache; total = input + cache read + cache write + output
- Codex's `inputTokens` **includes** cache; total = input + output
- The Codex server's `tokens` match the local Codex total

## History that could be recovered (at API list price)

**Claude**: merging four caches (v2/v4/v5/v6) and deduplicating by `messageId + requestId` gives **about $12,200**, back to 25 April. CodexBar showed $9,246 at the time.
- v2's daily totals double count (3 to 10 times too high per day). Deduplicated by message, it matches v4 request for request.
- The costs saved in v4 and v5 match CodexBar's current pricing exactly. v2 has no cost; assuming 93% of cache writes are 1-hour writes, the error on overlapping data is 0.05%.

**Codex**:
- Days with local detail (6/12–8/17, from 9/13): about $1,310. Uses v10's daily totals rather than v9's, which also counted history copied into forked conversations.
- The other days only have the server's token totals, 2.64B tokens. Priced by each month's main model, about $1,100, plausibly $340–2,230.
- Altogether about **$2,400**, plausibly $1,600–3,500.

## Pitfalls

- **Never** run `codexbar` with a changed `CODEX_HOME` or `CLAUDE_CONFIG_DIR`. It rewrites the real cache in `~/Library/Caches/CodexBar` and ignores a `HOME` override. It happened once on 2026-09-27; the app rebuilt its cache about 5 minutes later and nothing was lost.
- CodexBar overcounted 9/14: the server said 28.1M, CodexBar 66.9M. Found on 2026-10-04: CodexBar counts the running total a forked conversation inherits as new usage on the day of the fork. Same on 10/4: CodexBar 541.6M, 67.6M counted per request. Codex is now read by `codex-logs` directly (see docs/ARCHITECTURE.md)
- The newer `token_usage_record` entries in Codex logs (one per response, with `response_id`) line up with `token_count` almost one to one, and also record requests such as context compaction. Their per-request sum matches the server more closely, for example 51.4M on both sides for 9/20
- CodexBar 0.60.4's Codex long-context threshold: 272K for the gpt-5 family, 200K for the gpt-6 family; with that rule every day and model from 9/13 to 10/3 reproduced to the cent. CodexBar v0.78.0 follows OpenAI's published prices instead (272K for gpt-6 Sol and 6.1 Sol too), and TokenLedger now does the same
- The server's day boundary differs from CodexBar's London days, so comparing day by day shows neighbouring days swapping amounts
- The CodexBar CLI exists for macOS and Linux only, not Windows
