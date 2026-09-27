# TokenLedger 架构

TokenLedger 是一本只增不减的 AI 用量账本。它定期从 CodexBar 和 Codex 服务器读取每天的 token 数和估算花费，写进本地数据库。一天过了宽限期就定稿，之后来源那边的数据变小或消失，账本都保持原样。

## 目录结构

```
TokenLedger/
├── backend/                 Python 包 tokenledger（uv 管理）
│   ├── tokenledger/
│   │   ├── config.py        路径、时区、宽限期、机器标识（data/settings.json）
│   │   ├── models.py        核心数据结构：DayUsage、SeriesKey、CaptureResult、QuotaSample
│   │   ├── store/           SQLite 连接、迁移（db.py）和行级读写（repo.py）
│   │   ├── ledger.py        定稿规则：写入、冻结、来源变更
│   │   ├── capture.py       依次运行数据源，归档原始输出，合并进账本
│   │   ├── rawstore.py      原始输出的 gzip 归档
│   │   ├── sources/         数据源适配器（统一接口）
│   │   ├── backfill/        一次性补录：CodexBar 旧缓存
│   │   ├── corrections.py   人工采用来源的新数值
│   │   ├── views.py         给 API 和 MCP 用的查询与汇总
│   │   ├── api/             FastAPI：JSON 接口 + 静态前端
│   │   ├── mcp_server.py    MCP 工具
│   │   └── cli.py           命令行入口
│   └── tests/
├── frontend/                Vite + React + TypeScript + ECharts
│   └── src/
│       ├── api/             接口类型和请求
│       ├── charts/          各个图表（ECharts 按需引入）
│       ├── components/      页头、指标卡、数据来源等
│       ├── i18n/strings.ts  所有界面文字（中文 / English）
│       ├── theme/           亮色 / 暗色设计变量
│       └── lib/             格式化与数据整理
├── scripts/                 所有构建、测试、运行都走这里的脚本
├── data/                    账本数据（git 忽略）
├── docs/
└── research/                数据源调查记录
```

## 数据流

```
CodexBar CLI ─────┐
Codex 服务器 ─────┼─> sources/*  ─> CaptureResult ─> ledger.apply_capture() ─> SQLite ─> views ─> API / MCP ─> 前端
CodexBar 额度历史 ─┘                     │
                                        └─> data/raw/ 原始输出归档
CodexBar 旧缓存 ─> backfill/ ─> ledger.apply_backfill()
```

## 数据源接口

`sources/base.py` 定义了两类数据源：

```python
class UsageSource(Protocol):
    id: str
    def collect(self, ctx: SourceContext) -> list[CaptureResult]: ...

class QuotaSource(Protocol):
    id: str
    def collect(self, ctx: SourceContext) -> QuotaCapture: ...
```

数据源只负责读取和解析，不碰数据库。解析时发现数据不可信（扫描未完成、字段对不上、总数不等于各项之和）就抛出 `SourceError`，这次采集记为失败，其他数据源照常运行。

一条序列（series）由四项确定：

| 字段 | 例子 |
|---|---|
| source | `codexbar`、`codex-server`、以后的 `claude-logs` |
| provider | `claude`、`codex`、`antigravity` |
| machine | 这台 Mac 的 id；账号级数据为 `account` |
| scope | `machine`（本机日志）或 `account`（整个账号） |

目前的数据源：

| id | 读取 | 注意 |
|---|---|---|
| `codexbar` | `codexbar cost --provider <p> --days 365 --format json` | 运行前会去掉 `CODEX_HOME`、`CLAUDE_CONFIG_DIR`：CodexBar 只有一份共享缓存，换目录跑 CLI 会改写菜单栏 App 的缓存。时区和 CodexBar 设置不一致时直接报错 |
| `codex-server` | 启动 ChatGPT.app 自带的 `codex app-server`，发 `initialize` 和只读的 `account/usage/read` | 只有每天的 token 总数；日界线是服务器的（疑似 UTC） |
| `codexbar-quota` | `~/Library/Application Support/com.steipete.codexbar/history/*.json`，只读 | CodexBar 会压缩这份历史，账本逐条保留见过的样本 |

## 统一的 token 字段

两家的"输入"口径不同，入库前统一换算：

| 字段 | Claude（CodexBar） | Codex（CodexBar） |
|---|---|---|
| `input_uncached` | inputTokens | inputTokens − cacheReadTokens |
| `cache_read` | cacheReadTokens | cacheReadTokens |
| `cache_write` | cacheCreationTokens | 空 |
| `output` | outputTokens | outputTokens |
| `reasoning` | 空 | reasoningTokens（已含在 output 里，只做展示） |
| `total` | 四项之和 | inputTokens + outputTokens |

每天有一行 `model = "*"` 的全模型合计（带分项），外加每个模型一行（只有合计和金额）。Codex 服务器只给 `total`。

## 数据库

| 表 | 内容 |
|---|---|
| `series` | 序列定义 |
| `ledger` | 每条序列每天每个模型一行：token 各项、金额（微美元整数）、金额质量、`open`/`final`、来源（`capture:<id>`、`backfill:*`、`manual:revision:<id>`） |
| `revisions` | 定稿后来源的不同说法：`changed`、`missing`，以及被补录或人工替换掉的旧值 `replaced` |
| `captures` | 每次采集/补录的时间、状态、工具版本、原始归档路径、统计、错误 |
| `quota_samples` | 额度百分比样本 |
| `machines`、`meta` | 机器信息、schema 版本 |

迁移写在 `store/db.py` 的 `MIGRATIONS` 列表里，只追加不修改。

## 定稿规则

- 日期按 CodexBar 设置的时区划分（目前是 Europe/London）
- 某一天 D 在 D、D+1、D+2 这三天是 `open`，每次采集都用最新值覆盖，数值变小也接受（来源在宽限期内自我修正）
- 每次合并前先冻结到期的行，所以即使中间几天没采集，过期的行也不会被迟到的值覆盖
- 从 D+3 开始是 `final`。之后来源给出的值不同，只写入 `revisions`；同一个说法只记一次
- 判断"同一个说法"时的基准：最近一次记录的来源说法；补录替换过的行以替换当时来源的值为准；由补录新增、来源从来没有的行，来源不报它不算变更
- 第一次见到的日子如果已经过了宽限期，直接以 `final` 写入
- `DayUsage` 构造时校验金额和金额质量是否一致，杜绝"有金额却标成无金额"这类矛盾

## 金额

| 质量 | 含义 |
|---|---|
| `list_price` | CodexBar 按 API 标价算出（包括旧缓存里 CodexBar 当时存下的金额） |
| `recomputed` | 补录时按 CodexBar 的价格规则重算（`backfill/pricing.py`） |
| `unpriced` | 有 token，模型没有价格（例如 codex-auto-review） |
| `none` | 来源不提供金额（Codex 服务器） |

没有依据的金额一律留空，不做推测。

## 旧缓存补录

`tokenledger backfill` 读取 `data/legacy/codexbar-cache-*/` 里保存的 CodexBar 旧缓存（原件在 `~/Library/Caches/CodexBar`，随时可能被清掉，所以先复制进项目）。规则和验证过程见 `research/README.md`：

- Claude：某天的值 = CodexBar 当前的值 + 只在旧缓存里存在的请求（按 `messageId + requestId` 去重）。v4/v5 的请求带 CodexBar 当时的金额；v2 没有金额，按 v2 和 v4 共有请求算出的 1 小时缓存写入占比重算
- Codex：CodexBar 已经不报的日子，取覆盖这一天的最新旧缓存的按天合计（v10 优先于 v9，v9 把分叉会话复制的历史也算进去了）；金额逐请求按长上下文规则重算后按比例分到合计上

## 人工修正

`tokenledger accept <revision_id>`（或 MCP 的 `accept_revision`）：从这条变更对应的原始归档里重新解析出完整的一行，写进账本，旧值作为 `replaced` 保留。`missing` 类型的变更不能采用，账本不删历史。

## 看板取数规则（views.py）

- Claude：各台机器本机数据相加
- Codex：有账号级数据的日子用账号级 token 数，其余用本机数据；金额只来自本机数据，`cost_tokens` 标明金额覆盖了多少 token
- 额度峰值：每周窗口按重置时间分组取最高值，全程 0% 的窗口不计（Codex 每周窗口是滚动的，空闲时每个样本都报"7 天后重置"）；5 小时窗口按本地日期取当天最高

## HTTP 接口

只监听 127.0.0.1。除 `POST /api/capture` 外都是只读。

| 路径 | 内容 |
|---|---|
| `GET /api/status` | 机器、时区、账本行数、最近采集、失败 |
| `GET /api/summary?start&end` | 各平台合计、金额覆盖率、token 构成、峰值日、连续天数 |
| `GET /api/daily?start&end` | 每天每个平台的 token、金额、质量、统计中标记 |
| `GET /api/models?start&end` | 各模型合计（本机数据） |
| `GET /api/quota?start&end&max_points` | 额度原始样本（抽稀） |
| `GET /api/quota/peaks?start&end` | 额度峰值 |
| `GET /api/revisions?limit`、`GET /api/captures?limit` | 变更和采集记录 |
| `POST /api/capture` | 立即采集 |

交互式文档在 `/api/docs`。

## MCP 工具

`scripts/mcp.sh` 在 stdio 上提供：`ledger_status`、`usage_summary`、`daily_usage`、`model_breakdown`、`quota_history`、`quota_peaks`、`list_revisions`、`list_captures`（只读），以及 `run_capture`、`accept_revision`（会写账本）。

## 以后接入 Windows

Windows 上没有 CodexBar。计划新增一个 `claude-logs` 数据源，直接解析 `%USERPROFILE%\.claude\projects\**\*.jsonl`，规则与 CodexBar 一致：按 `message.id + requestId` 去重，按 models.dev 价格计价，区分 5 分钟和 1 小时缓存写入。它产出的 `CaptureResult` 带自己的机器 id，交给同一个 `ledger.apply_capture()`。数据怎么从 Windows 回到这台 Mac（共享文件夹里的导出文件，或者通过 Tailscale 调用这边的接口）到时候再定。解析器可以先在这台 Mac 上和 CodexBar 的结果对照验证。

## 运行开销与产物

见 README 的"产物和清理"。
