// Every piece of UI text lives here. Components never hardcode text.
//
// The console style pairs a fixed English code label (TOTAL TOKENS, QUOTA, ...) with a
// localized description. Code labels are part of the graphic design and stay English in
// both languages; `sub` texts carry the language. In English most subs are empty because
// the code label already says it.

export type Lang = "zh" | "en";

const zh = {
  appTitle: "TOKENLEDGER",
  appSubtitle: "AI 用量账本",
  range: { d30: "30D", d90: "90D", d180: "6M", y1: "1Y", all: "All" },
  theme: { system: "跟随系统", light: "亮色", dark: "暗色" },
  langSwitch: "EN",
  top: {
    machine: "Machine",
    time: "Local time",
    lastOk: "Last OK",
    lastOkSub: "上次成功采集",
    capture: "Capture",
    captureSub: "立即采集",
    capturing: "Capturing",
    captureFailed: "采集失败",
    failing: (n: number) => `${n} 个来源采集失败`,
  },
  panels: {
    total: "总 Token",
    quota: "额度",
    activity: "每日活动",
    tokens: "每日 Token",
    cost: "每周花费",
    models: "模型",
    sources: "数据来源",
  },
  total: {
    span: "Span",
    basis: "Basis",
    basisValue: "Account + machine",
    exact: "Exact",
    unit: "Unit",
    unitValue: "1亿 = 100M",
    days: "Days",
    rows: "Rows",
    cost: "估算花费",
    costNote: "API list",
    active: "活跃天数",
    streak: (n: number) => `连续 ${n} 天`,
    peak: "峰值日",
    avg: "日均",
    avgNote: "按活跃天计",
  },
  today: { date: "Date", tokens: "Tokens", cost: "Cost", vsAvg: "vs 7d avg", state: "State", open: "OPEN 统计中", final: "FINAL 已定稿" },
  provider: {
    basisAccount: "OpenAI account · 账号级",
    basisMachine: "Machine logs · 本机日志",
    tokens: "总量",
    share: "占比",
    listPrice: "API 标价",
    coverage: (p: string) => `覆盖 ${p} 的 Token`,
    perMillion: "每百万 Token",
    cacheRead: "缓存读取占比",
    activeDays: (n: number) => `${n} 天活跃`,
  },
  quota: {
    weekly: "每周",
    session: "5 小时",
    peakMark: "▼ 上周峰值",
    lastWeekPeak: "PEAK LW",
    reset: "RESET",
    wasReset: "已重置",
    others: "其他",
  },
  activity: { less: "Less", more: "More", peak: "Peak", active: "Active", monthly: "每月合计", inProgress: "进行中", weekday: "星期均值" },
  chart: { avg7: "7D AVG", peak: "PEAK" },
  cost: { now: "本周", prev: "上周", avg: "周均", block: "每格", dashed: "虚线框 = 本周" },
  models: { model: "Model", shareTokens: "Share of tokens", shareCost: "Share of cost", tokens: "Tokens", cost: "Cost", unpriced: "未定价", count: (n: number) => `${n} MODELS` },
  sources: {
    ok: "OK",
    fail: "FAIL",
    names: {
      "codexbar/claude": ["CodexBar · Claude", "本机 · 每日每模型"],
      "codex-logs/codex": ["Codex 本机日志", "本机 · 每日每模型"],
      "codexbar/antigravity": ["CodexBar · Antigravity", "本机 · 每日每模型"],
      "codex-server/codex": ["OpenAI 服务器 · Codex", "账号 · 每日总数"],
      "codexbar-quota/": ["CodexBar · 额度", "5 小时与每周"],
    } as Record<string, [string, string]>,
    lastOk: (when: string) => `上次成功 ${when}`,
    never: "从未成功",
    rows: "账本行",
    final: "已定稿",
    open: "统计中",
    revisions: "来源变更",
    codexCoverage: (p: string) => `Codex 金额覆盖 ${p} 的 Token`,
  },
  tooltip: { total: "合计", open: "统计中", account: "账号级", noUsage: "无用量", week: "WEEK" },
  providers: { claude: "Claude", codex: "Codex", antigravity: "Antigravity", opencodego: "OpenCode Go" } as Record<string, string>,
  loading: "加载中…",
  error: "无法连接后端",
  empty: "还没有数据，先点一次“立即采集”",
  footer: "COST AT API LIST PRICE · 金额按 API 标价估算 · 每天过 3 天定稿",
};

export type Strings = typeof zh;

const en: Strings = {
  appTitle: "TOKENLEDGER",
  appSubtitle: "AI usage ledger",
  range: { d30: "30D", d90: "90D", d180: "6M", y1: "1Y", all: "All" },
  theme: { system: "System", light: "Light", dark: "Dark" },
  langSwitch: "中",
  top: {
    machine: "Machine",
    time: "Local time",
    lastOk: "Last OK",
    lastOkSub: "",
    capture: "Capture",
    captureSub: "",
    capturing: "Capturing",
    captureFailed: "Capture failed",
    failing: (n: number) => `${n} source${n === 1 ? "" : "s"} failing`,
  },
  panels: { total: "", quota: "", activity: "", tokens: "", cost: "", models: "", sources: "" },
  total: {
    span: "Span",
    basis: "Basis",
    basisValue: "Account + machine",
    exact: "Exact",
    unit: "Unit",
    unitValue: "tokens",
    days: "Days",
    rows: "Rows",
    cost: "",
    costNote: "API list",
    active: "",
    streak: (n: number) => `streak ${n}`,
    peak: "",
    avg: "",
    avgNote: "per active day",
  },
  today: { date: "Date", tokens: "Tokens", cost: "Cost", vsAvg: "vs 7d avg", state: "State", open: "OPEN", final: "FINAL" },
  provider: {
    basisAccount: "OpenAI account",
    basisMachine: "This Mac's logs",
    tokens: "",
    share: "of all tokens",
    listPrice: "API list price",
    coverage: (p: string) => `covers ${p} of tokens`,
    perMillion: "per million tokens",
    cacheRead: "of tokens",
    activeDays: (n: number) => `${n} active days`,
  },
  quota: {
    weekly: "",
    session: "",
    peakMark: "▼ Last week peak",
    lastWeekPeak: "PEAK LW",
    reset: "RESET",
    wasReset: "reset",
    others: "Others",
  },
  activity: { less: "Less", more: "More", peak: "Peak", active: "Active", monthly: "", inProgress: "in progress", weekday: "" },
  chart: { avg7: "7D AVG", peak: "PEAK" },
  cost: { now: "", prev: "", avg: "", block: "", dashed: "dashed = this week" },
  models: { model: "Model", shareTokens: "Share of tokens", shareCost: "Share of cost", tokens: "Tokens", cost: "Cost", unpriced: "unpriced", count: (n: number) => `${n} MODELS` },
  sources: {
    ok: "OK",
    fail: "FAIL",
    names: {
      "codexbar/claude": ["CodexBar · Claude", "this Mac · per model"],
      "codex-logs/codex": ["Codex logs", "this Mac · per model"],
      "codexbar/antigravity": ["CodexBar · Antigravity", "this Mac · per model"],
      "codex-server/codex": ["OpenAI server · Codex", "account · daily totals"],
      "codexbar-quota/": ["CodexBar · quota", "5-hour and weekly"],
    },
    lastOk: (when: string) => `last success ${when}`,
    never: "never succeeded",
    rows: "",
    final: "",
    open: "",
    revisions: "",
    codexCoverage: (p: string) => `Codex cost covers ${p} of tokens`,
  },
  tooltip: { total: "Total", open: "open", account: "account", noUsage: "no usage", week: "WEEK" },
  providers: { claude: "Claude", codex: "Codex", antigravity: "Antigravity", opencodego: "OpenCode Go" },
  loading: "Loading…",
  error: "Cannot reach the backend",
  empty: "No data yet. Press Capture once.",
  footer: "COST AT API LIST PRICE · days finalize after 3 days",
};

export const STRINGS: Record<Lang, Strings> = { zh, en };

export function providerName(t: Strings, provider: string): string {
  return t.providers[provider] ?? provider;
}

// Two-letter codes on tags; fixed across languages.
export function providerCode(provider: string): string {
  return ({ claude: "CL", codex: "CX", antigravity: "AG" } as Record<string, string>)[provider] ?? provider.slice(0, 2).toUpperCase();
}
