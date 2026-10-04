# TokenLedger

一本只增不减的 AI 用量账本。它每天从 CodexBar、Codex 本机日志和 OpenAI 服务器读取 Claude、Codex、Antigravity 的 token 数和按 API 标价估算的花费，存进本地数据库。每一天过了 3 天就定稿，之后日志被清理、CodexBar 改了算法，账本里的历史都保持原样。

配套一个本地网页看板，以及一个给 Claude 用的 MCP 接口。

## 快速使用

```bash
scripts/dashboard.sh open
```

启动看板并打开 http://127.0.0.1:8765/ 。第一次运行会先构建前端。

### 看板服务的开启和关闭

| 命令 | 作用 |
|---|---|
| `scripts/dashboard.sh start` | 在后台启动（已在运行就什么也不做） |
| `scripts/dashboard.sh open` | 需要时启动，并在浏览器里打开 |
| `scripts/dashboard.sh stop` | 正常关闭 |
| `scripts/dashboard.sh restart` | 重启；更新了后端代码之后需要这一步 |
| `scripts/dashboard.sh status` | 是否在运行、地址、运行了多久 |
| `scripts/dashboard.sh logs` | 这次（或上一次）运行的日志 |
| `scripts/dashboard.sh run` | 改在当前终端里运行，Ctrl+C 结束 |

也可以直接跟 Claude 说"打开看板"或"关掉看板"，MCP 里有对应的工具。

服务在后台独立运行，关掉终端、重启 Claude 都不影响它，电脑睡眠时暂停、唤醒后继续。以下情况它会结束，每种都会在日志里写明原因：

- 执行 `stop` 或 `restart`
- 连续 12 小时没有任何页面请求，自动退出，免得忘了关一直挂着。用 `--idle-hours` 修改，设为 0 就一直运行
- `run` 模式下按 Ctrl+C，或者关掉它所在的终端
- 注销或关机

前端改动重新构建后刷新页面即可；后端改动需要 `restart`。

顶栏的"上次成功采集"是所有数据源最近一次都成功的时间，失败的尝试不会刷新它。某个数据源失败时，顶栏会一直显示红色提示，"数据来源"模块里能看到具体错误和上次成功的时间，直到它下次采集成功。

### 其他命令

| 命令 | 作用 |
|---|---|
| `scripts/tokenledger.sh capture` | 立即采集一次（CodexBar、Codex 本机日志、Codex 服务器、额度历史） |
| `scripts/tokenledger.sh status` | 账本概况、最近采集和失败 |
| `scripts/tokenledger.sh backfill --dry-run` | 预览从 CodexBar 旧缓存补录的结果 |
| `scripts/tokenledger.sh accept <id>` | 采用某条来源变更的新数值（见下文） |
| `scripts/test.sh` | 后端测试和前端类型检查 |
| `scripts/build.sh` | 构建前端 |
| `scripts/install-agent.sh` | 安装定时采集（每天 9:00、21:00；项目在 ~/Documents 下时会被 macOS 拦截，见架构文档） |
| `scripts/uninstall-agent.sh` | 移除定时采集 |
| `scripts/clean.sh` | 删除构建产物和依赖，账本数据不动 |
| `scripts/grade-background.py` | 重新生成看板背景图（用法见文件开头） |

## 数据从哪里来

| 来源 | 内容 | 范围 |
|---|---|---|
| `codexbar cost` | Claude 每天每个模型的 token 和标价金额 | 这台 Mac 的本地日志 |
| Codex 本机日志（`~/.codex/sessions`） | Codex 每天每个模型的 token 和标价金额，逐个请求去重 | 这台 Mac |
| Codex 服务器（`codex app-server` 的 `account/usage/read`） | 每天的 token 总数 | 整个账号、所有设备 |
| CodexBar 额度历史 | 5 小时和每周额度的使用百分比 | 整个账号 |
| CodexBar 旧缓存（一次性补录） | 已被删除的日志里的请求 | 4 月 25 日起的 Claude，6 月到 8 月的 Codex |

看板上 Codex 的 token 数优先用服务器的账号级数据；服务器的数据要晚半天到一天才完整，所以还在统计中的日子取服务器和本机里较大的那个。金额只来自本机明细，覆盖比例显示在"数据来源"模块里。

Codex 的本机数据在 2026-10-04 之前来自 CodexBar。CodexBar 会把分叉对话从原对话继承来的累计 token 当成当天的新用量（10 月 4 日一天多算了约 4.7 亿），现在改由 TokenLedger 自己读日志，每个请求只算一次。日志已经不在的日子仍沿用 CodexBar 当时的记录。

## 定稿规则

- 某一天在当天和之后两天内是"统计中"，每次采集都更新
- 过了这三天后的第一次采集负责定稿：来源的数不比账本小，就采用来源的数再定稿；变小或者这天不见了，保留账本的数，记一条"来源变更"
- 定稿后数字固定，来源再给出不同的数值，只记一条"来源变更"
- 如果确认是来源修正了错误，可以用 `accept` 采用新值，旧值会保留在记录里

多久采集一次都能记准。一到两周点一次"立即采集"就够；清理 Claude 或 Codex 的对话之前记得先采集一次，Claude 命令行会话的日志 30 天后会被自动清理。

## 目录

```
backend/    Python 后端：账本、数据源、API、MCP、命令行
frontend/   React 看板（图表为手写 SVG）
scripts/    所有构建、运行、测试脚本（缓存都限制在项目内）
data/       账本数据库、原始采集归档、旧缓存副本（不进 git）
docs/       架构说明
research/   数据源调查记录和当时的原始快照
```

架构、数据模型和接口见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 产物和清理

| 产物 | 位置 | 由谁清理 |
|---|---|---|
| 账本、原始归档、旧缓存副本 | `data/` | 用户数据，手动决定 |
| Python 环境、uv/npm 缓存、临时文件 | `backend/.venv`、`.build/` | `scripts/clean.sh` |
| 前端依赖和构建 | `frontend/node_modules`、`frontend/dist` | `scripts/clean.sh` |
| 定时采集 | `~/Library/LaunchAgents/io.github.dongkunxu.tokenledger.capture.plist` | `scripts/uninstall-agent.sh` |
| 最近一次定时采集的输出 | `.build/logs/` | `scripts/clean.sh` |
| 看板服务的状态文件和日志（只保留最近一次运行） | `.build/run/` | 服务退出时删除状态文件；`scripts/clean.sh` 会先停服务再清理 |

采集一次大约 2 秒。每次采集的原始归档约 16 KB，按每天两次算，一年约 12 MB。
