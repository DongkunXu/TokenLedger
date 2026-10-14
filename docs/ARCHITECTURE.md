# TokenLedger 架构

TokenLedger 是一本只增不减的 AI 用量账本。它定期从 CodexBar、Codex 本机日志和 Codex 服务器读取每天的 token 数和估算花费，写进本地数据库。一天过了宽限期就定稿，之后来源那边的数据变小或消失，账本都保持原样。

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
│   │   ├── pricing.py       按 CodexBar 的价格表和规则计算标价金额
│   │   ├── corrections.py   人工采用来源的新数值
│   │   ├── views.py         给 API 和 MCP 用的查询与汇总
│   │   ├── api/             FastAPI：JSON 接口 + 静态前端
│   │   ├── mcp_server.py    MCP 工具
│   │   └── cli.py           命令行入口
│   └── tests/
├── frontend/                Vite + React + TypeScript，图表全部是手写 SVG
│   └── src/
│       ├── api/             接口类型和请求
│       ├── panels/          页面上的八个模块（00 总量 … 07 数据来源）
│       ├── components/      模块外框、导航栏、顶栏、开关、像素格表、提示框、趣味换算
│       ├── i18n/strings.ts  所有界面文字（中文 / English）
│       ├── theme/           亮色 / 暗色设计变量，主题切换
│       ├── styles/          页面骨架（app.css）和模块内部（panels.css）
│       ├── assets/bg.jpg    背景图（scripts/grade-background.py 生成）
│       └── lib/             格式化、数据整理、尺寸与动效、趣味换算表（fun.ts）
├── scripts/                 所有构建、测试、运行都走这里的脚本
├── data/                    账本数据（git 忽略）
├── docs/
└── research/                数据源调查记录
```

## 数据流

```
CodexBar CLI ─────┐
Codex 本机日志 ───┤
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
| source | `codexbar`、`codex-logs`、`codex-server`、以后的 `claude-logs` |
| provider | `claude`、`codex`、`antigravity` |
| machine | 这台 Mac 的 id；账号级数据为 `account` |
| scope | `machine`（本机日志）或 `account`（整个账号） |

目前的数据源：

| id | 读取 | 注意 |
|---|---|---|
| `codexbar` | `codexbar cost --provider <p> --days 365 --format json`，provider 为 `claude`、`antigravity` | 运行前会去掉 `CODEX_HOME`、`CLAUDE_CONFIG_DIR`：CodexBar 只有一份共享缓存，换目录跑 CLI 会改写菜单栏 App 的缓存。时区和 CodexBar 设置不一致时直接报错。2026-10-04 之前也读 Codex，见下文 |
| `codex-logs` | `~/.codex/sessions/**/*.jsonl` 和 `~/.codex/archived_sessions/*.jsonl`，只读；价格表用 CodexBar 缓存的 `~/Library/Caches/CodexBar/model-pricing/models-dev-v1.json` | 每个请求只算一次，规则见下文。归档的原始输出是按天按模型的汇总，`accept` 时从它重建行。价格表不在时采集报错 |
| `codex-server` | 启动 ChatGPT.app 自带的 Codex CLI（按已知位置依次查找：`Contents/Resources/codex-cli/bin/codex`，更早版本是 `Contents/Resources/codex`），以 `app-server` 模式发 `initialize` 和只读的 `account/usage/read` | 只有每天的 token 总数；日界线是服务器的（疑似 UTC）。ChatGPT 更新后如果位置又变了，采集会报错并列出查找过的路径，在 `BINARY_CANDIDATES` 里加上新位置即可 |
| `codexbar-quota` | `~/Library/Application Support/com.steipete.codexbar/history/*.json`，只读 | CodexBar 会压缩这份历史，账本逐条保留见过的样本 |

## Codex 本机日志（codex-logs）

Codex 的本机数据在 2026-10-04 之前由 CodexBar 提供。CodexBar 会把分叉对话继承的累计 token 当成分叉当天的新用量：分叉出来的日志文件里，第一个 `token_count` 事件的累计值就带着原对话的全部用量（10 月 4 日 CodexBar 报 5.42 亿，日志里实际 6760 万，服务器 1330 万且还在补）。9 月 14 日的高估也是同一个原因。现在由 `sources/codex_logs.py` 自己读日志：

- 新版 Codex 每次请求写一条 `token_usage_record`，带唯一的 `response_id`。文件里有这种记录就只用它们，所有文件之间按 `response_id` 去重，复制到别的文件里的历史不会重复计算
- 没有这种记录的旧文件用 `token_count` 事件：相邻两次累计值之差就是一次请求的用量；文件里的第一个事件只算它自己的 `last_token_usage`，所以继承来的累计值不会算进去。这类事件按"时间 + 累计值"在文件之间去重
- 每个请求按时间戳归到账本时区的日期，按模型逐个计价后再按天按模型汇总；`total` 按 输入 + 输出 计算，和 CodexBar 口径相同
- 新格式的记录还包括上下文压缩等请求，所以比 CodexBar 的数略多（每天 1% 到 3%），和服务器对得更紧

CodexBar 的 Codex 序列从此不再采集，账本保留它的历史（迁移 2 把它剩下的 open 行定稿）。看板按天选数据：日志里有这一天就用 `codex-logs`，日志已经不在的日子用 CodexBar 当时的记录（`views.MACHINE_SOURCE_ORDER`）。

## 统一的 token 字段

两家的"输入"口径不同，入库前统一换算：

| 字段 | Claude（CodexBar） | Codex（本机日志，以前是 CodexBar） |
|---|---|---|
| `input_uncached` | inputTokens | inputTokens − cacheReadTokens |
| `cache_read` | cacheReadTokens | cacheReadTokens |
| `cache_write` | cacheCreationTokens | 空 |
| `output` | outputTokens | outputTokens |
| `reasoning` | 空 | reasoningTokens（已含在 output 里，只做展示） |
| `total` | 四项之和 | inputTokens + outputTokens |

每天有一行 `model = "*"` 的全模型合计（带分项），外加每个模型一行（CodexBar 的模型行只有合计和金额，`codex-logs` 的模型行带全部分项）。Codex 服务器只给 `total`。

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
- 过了宽限期后，这条序列的第一次采集负责定稿，按这天的全模型合计行（`model = "*"`）判断：
  - 来源的合计不小于账本：采用来源这一天的所有行，再冻结。晚到的用量能记进去，手动隔很久采集一次也不会把只记了半天的数冻住
  - 来源的合计变小，或者这天不见了：保留账本的行并冻结，来源的说法写进 `revisions`。这通常说明日志被删了
- 定稿按序列各自进行：某条序列没被采集，它的 open 行就一直等到它自己的下一次采集
- 第一次见到的日子如果已经过了宽限期，直接以 `final` 写入
- `final` 之后来源给出的值不同，只写入 `revisions`；同一个说法只记一次
- 判断"同一个说法"：和来源对这一天说过的所有值比较，出现过的就不再记。包括账本里由采集写入的值、补录替换当时来源的值、以前记过的变更；补录新增、来源从来没有的行，来源不报它也不算变更。这样来源在两个值之间来回跳（CodexBar 的 Codex 9/14 就是这样）只会留下两条记录
- `DayUsage` 构造时校验金额和金额质量是否一致，杜绝"有金额却标成无金额"这类矛盾；每个来源的每一天都必须带全模型合计行

## 采集频率

采集频率不影响准确性，只要日志还在时采集到就行。手动点"立即采集"或让 Claude 通过 MCP 采集即可，一到两周一次足够；清理对话前先采集。

`scripts/install-agent.sh` 可以装 launchd 定时任务，但项目放在 `~/Documents` 下时，macOS 不允许 launchd 启动的 bash 读取这个目录（exit 126，Operation not permitted）。要用定时任务，需要把运行时和数据移到 `~/Library/Application Support`，或者把项目移出 Documents。

## 金额

| 质量 | 含义 |
|---|---|
| `list_price` | 按 API 标价算出：CodexBar 算的（包括旧缓存里当时存下的金额），或 `codex-logs` 按 CodexBar 的价格表和规则逐个请求算的 |
| `recomputed` | 补录时按 CodexBar 的价格规则重算 |
| `unpriced` | 有 token，模型没有价格（例如 codex-auto-review） |
| `none` | 来源不提供金额（Codex 服务器） |

没有依据的金额一律留空，不做推测。

价格规则在 `pricing.py`。Codex 的长上下文规则：一次请求的输入超过门槛，整次请求按 `context_over_200k` 的价格算。门槛 gpt-5 系列是 272K，之后的模型是 200K，这是从 CodexBar 的结果反推出来的，按它计算，2026-09-13 到 10-03 每天每个模型的金额都和 CodexBar 一致（分叉高估的 9/14 除外）。

## 旧缓存补录

`tokenledger backfill` 读取 `data/legacy/codexbar-cache-*/` 里保存的 CodexBar 旧缓存（原件在 `~/Library/Caches/CodexBar`，随时可能被清掉，所以先复制进项目）。规则和验证过程见 `research/README.md`：

- Claude：某天的值 = CodexBar 当前的值 + 只在旧缓存里存在的请求（按 `messageId + requestId` 去重）。v4/v5 的请求带 CodexBar 当时的金额；v2 没有金额，按 v2 和 v4 共有请求算出的 1 小时缓存写入占比重算
- Codex：CodexBar 已经不报的日子，取覆盖这一天的最新旧缓存的按天合计（v10 优先于 v9，v9 把分叉会话复制的历史也算进去了）；金额逐请求按长上下文规则重算后按比例分到合计上

## 人工修正

`tokenledger accept <revision_id>`（或 MCP 的 `accept_revision`）：从这条变更对应的原始归档里重新解析出完整的一行，写进账本，旧值作为 `replaced` 保留。`missing` 类型的变更不能采用，账本不删历史。

## 看板取数规则（views.py）

- Claude：各台机器本机数据相加
- Codex：有账号级数据的日子用账号级 token 数，其余用本机数据。服务器的数据要晚半天到一天才完整，所以还在统计中的日子取两者中较大的那个；定稿的日子只用账号级数据，因为服务器的日界线是 UTC，和本机数据混用会把午夜前后的用量算两次。金额只来自本机数据，`cost_tokens` 标明金额覆盖了多少 token
- 同一台机器、同一平台有多个本机来源时，每天取 `MACHINE_SOURCE_ORDER` 里排在前面且有这一天的来源；不再采集的来源（`RETIRED_SOURCES`）不出现在数据来源健康列表里
- 额度峰值：每周窗口按重置时间分组取最高值，全程 0% 的窗口不计（Codex 每周窗口是滚动的，空闲时每个样本都报"7 天后重置"）；5 小时窗口按本地日期取当天最高

## HTTP 接口

只监听 127.0.0.1。除 `POST /api/capture` 外都是只读。

| 路径 | 内容 |
|---|---|
| `GET /api/status` | 机器、时区、账本行数，以及每个数据源最近一次尝试的结果（`sources`：是否成功、时间、错误、上次成功时间） |
| `GET /api/summary?start&end` | 各平台合计、金额覆盖率、token 构成、峰值日、连续天数 |
| `GET /api/daily?start&end` | 每天每个平台的 token、金额、质量、统计中标记 |
| `GET /api/models?start&end` | 各模型合计（本机数据） |
| `GET /api/quota?start&end&max_points` | 额度原始样本（抽稀） |
| `GET /api/quota/peaks?start&end` | 额度峰值 |
| `GET /api/quota/now` | 每个额度窗口的最新读数和重置时间 |
| `GET /api/revisions?limit`、`GET /api/captures?limit` | 变更和采集记录 |
| `POST /api/capture` | 立即采集 |

交互式文档在 `/api/docs`。

## 看板界面

控制台风格：全部方角，大面积只用黑白灰，Claude 用橙、Codex 用蓝，橙色同时是唯一的强调色。标题和数字用 Chakra Petch，编号、代码标签这类小装饰用像素字体 Silkscreen，数据明细用 JetBrains Mono，三款字体都打包在前端里，离线可用。每个模块是一张薄毛玻璃面板，带噪点和跟随鼠标的边缘高光，背后是按调色板调过色的照片，亮色和暗色主题各自在 CSS 里压亮或压暗。

图表没有用图表库，都是 React 直接画的 SVG：每日 Token 堆叠柱、每周花费方块矩阵、热力图、每月 LED 柱、额度分段条。需要填满面板的图表用 `useSize` 读取所在区域的实际尺寸再绘制，所以同一行里高度不同的模块会互相对齐，窗口大小变化时也会重新排布。

`index.html` 带 `Cache-Control: no-cache`，每次重新构建后刷新就能拿到新页面；带哈希文件名的资源文件照常缓存。

## 看板服务的生命周期

`tokenledger serve`（`api/server.py`）是服务本身，`tokenledger dashboard`（`dashboard.py`）负责管理它：

- `dashboard start` 用独立的会话（`start_new_session`）在后台启动服务，和启动它的终端脱钩，等到 `/api/status` 有响应才返回；同一时间只运行一个，端口被别的程序占用时直接报错
- 服务启动时写 `.build/run/dashboard.json`（pid、端口、开始时间、空闲上限），退出时删除。管理命令通过它找到服务，并先确认这个 pid 确实是 TokenLedger 的服务再发信号；服务异常退出留下的状态文件会被识别并清掉
- 退出方式都是正常关闭：SIGTERM（`dashboard stop`）、Ctrl+C、SIGHUP（所在终端被关）、空闲超时。uvicorn 处理完 SIGINT/SIGTERM 后会把信号重新抛出，服务预先装了 Python 层的处理函数接住，保证清理和日志都能完成。`stop` 等 10 秒还没退出才强制结束
- 空闲计时按墙上时间算，只计页面和 API 请求；`dashboard status` 的健康检查带 `X-TokenLedger-Probe` 头，不算活动，否则查看状态本身就会让它永远不退出
- 日志写在 `.build/run/dashboard.log`，每次启动覆盖，只保留最近一次运行

## MCP 工具

`scripts/mcp.sh` 在 stdio 上提供：`ledger_status`、`usage_summary`、`daily_usage`、`model_breakdown`、`quota_history`、`quota_peaks`、`quota_now`、`list_revisions`、`list_captures`、`dashboard_status`（只读），`run_capture`、`accept_revision`（会写账本），以及 `dashboard_start`、`dashboard_stop`（开关看板服务）。

## 以后接入 Windows

Windows 上没有 CodexBar。计划新增一个 `claude-logs` 数据源，直接解析 `%USERPROFILE%\.claude\projects\**\*.jsonl`，规则与 CodexBar 一致：按 `message.id + requestId` 去重，按 models.dev 价格计价，区分 5 分钟和 1 小时缓存写入。它产出的 `CaptureResult` 带自己的机器 id，交给同一个 `ledger.apply_capture()`。数据怎么从 Windows 回到这台 Mac（共享文件夹里的导出文件，或者通过 Tailscale 调用这边的接口）到时候再定。解析器可以先在这台 Mac 上和 CodexBar 的结果对照验证。

## 运行开销与产物

见 README 的"产物和清理"。
