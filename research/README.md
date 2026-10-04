# TokenLedger 数据源调查（2026-09-27）

这里记录写工具之前的数据源调查结论，以及当时保存的原始数据。结论已经体现在 `backend/` 的实现里，见 `docs/ARCHITECTURE.md`。

## 目录

- `snapshots-2026-09-27/codex-server-account-usage.json`：Codex 服务器端账号用量，按天统计，2026-02-26 到 2026-09-26 共 185 天，累计 5,232,931,596 token
- `snapshots-2026-09-27/codexbar-cost-{claude,codex}.json`：当天 `codexbar cost --days 365 --format json` 的导出（CodexBar 0.60.4）

## 数据源

| 来源 | 范围 | 精度 | 可靠性 |
|---|---|---|---|
| CodexBar 导出（本机日志） | 仅本机，Claude + Codex | 每天各类 token、按模型的 token 和金额 | 日志被删后数据会消失；已过去的日子也可能改变（9/14 一天内从 51.6M 变成 66.9M） |
| Codex 服务器 `account/usage/read` | 整个账号，所有设备 | 每天只有一个 token 总数，没有分类，也没有金额 | 和本机完整日子的数字相差约 5%；日界线疑似 UTC |
| Codex 服务器按对话查询（带 `threadId`） | — | — | 本账号返回 null，用不了 |
| CodexBar 旧缓存 `~/Library/Caches/CodexBar/cost-usage/*-v*.json` | 各版本扫描时的窗口 | 每条请求一行 | 有过重复计数的 bug，需要自己去重，见下文 |
| Codex `state_5.sqlite` 里的 `threads.tokens_used` | 每个对话一个累计值 | 很粗 | 恢复过的对话会重新计数，分叉对话会重复计算，不采用 |
| CodexBar 额度百分比历史 `history/*.json` | 账号整体，从 4 月 9 日起 | 只有百分比 | 有压缩和上限 |

## 两家的口径不同

- Claude 的 `inputTokens` **不含**缓存，总数 = 输入 + 缓存读 + 缓存写 + 输出
- Codex 的 `inputTokens` **含**缓存，总数 = 输入 + 输出
- Codex 服务器的 `tokens` 和 Codex 本机的总数口径一致

## 能补回的历史（按 API 标价估算）

**Claude**：合并四份缓存（v2/v4/v5/v6）并按 `messageId + requestId` 去重后，**约 $12,200**，最早到 4 月 25 日。CodexBar 现在显示 $9,246。
- v2 的按天汇总有重复计数（每天虚高 3 到 10 倍）。按消息去重后，和 v4 逐条一致。
- v4 和 v5 自带的金额和 CodexBar 现在的算法完全一致。v2 没有金额，按缓存写入 93% 是 1 小时档来推算，在重叠数据上误差 0.05%。

**Codex**：
- 本机有明细的日子（6/12–8/17、9/13 以后）：约 $1,310。用 v10 的按天汇总，不用 v9，因为 v9 把分叉对话复制过去的历史也算进去了。
- 其余日子只有服务器上的 token 总数，共 26.4 亿 token。按各月主要模型估算约 $1,100，可能范围 $340–2,230。
- 全部合计约 **$2,400**，可能范围大约 $1,600–3,500。

## 已知的坑

- **不要**用改过的 `CODEX_HOME` 或 `CLAUDE_CONFIG_DIR` 去跑 `codexbar`。它会改写真实的缓存 `~/Library/Caches/CodexBar`，而且不认 `HOME` 覆盖。2026-09-27 实际发生过一次，App 大约 5 分钟后自己重建，数据没有丢。
- CodexBar 的 9/14 数据被高估：服务器是 28.1M，CodexBar 是 66.9M。2026-10-04 查明原因：CodexBar 把分叉对话继承的累计 token 当成分叉当天的新用量。10/4 同样，CodexBar 报 541.6M，逐请求计算是 67.6M。之后 Codex 改由 `codex-logs` 直接读日志（见 docs/ARCHITECTURE.md）
- Codex 日志里新版的 `token_usage_record`（每个响应一条，带 `response_id`）和 `token_count` 基本一一对应，另外还记录了上下文压缩等请求。逐请求合计和服务器对得更紧，例如 9/20 都是 51.4M
- CodexBar 的 Codex 长上下文门槛：gpt-5 系列 272K，gpt-6 系列 200K。用这个规则，9/13 到 10/3 每天每个模型的金额都能复现到分
- 服务器按天统计的日界线和 CodexBar 的伦敦时区不一样，逐日对比会出现相邻两天的数字对调。
- CodexBar CLI 只有 macOS 和 Linux 版本，没有 Windows 版。
