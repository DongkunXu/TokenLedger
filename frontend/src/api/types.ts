// Shapes returned by the backend (backend/tokenledger/views.py).

export type CostQuality = "list_price" | "recomputed" | "unpriced" | "none";

export interface TokenParts {
  input_uncached: number;
  cache_read: number;
  cache_write: number;
  output: number;
  reasoning: number;
}

export interface DayValue {
  tokens: number;
  basis: "account" | "machine";
  machine_tokens: number | null;
  account_tokens: number | null;
  cost_usd: number | null;
  cost_tokens: number;
  cost_quality: CostQuality | null;
  open: boolean;
  parts: TokenParts | null;
}

export interface Daily {
  start: string;
  end: string;
  days: string[];
  providers: { provider: string; values: (DayValue | null)[] }[];
}

export interface ProviderSummary {
  tokens: number;
  cost_usd: number;
  cost_coverage: number | null;
  active_days: number;
  parts: Partial<TokenParts>;
}

export interface Summary {
  start: string;
  end: string;
  tokens: number;
  cost_usd: number;
  active_days: number;
  peak_day: { day: string | null; tokens: number };
  current_streak: number;
  providers: Record<string, ProviderSummary>;
}

export interface ModelRow {
  provider: string;
  model: string;
  tokens: number;
  cost_usd: number | null;
  days: number;
}

export interface QuotaPeaks {
  weekly: { provider: string; points: [string, number][] }[];
  session: { provider: string; points: [string, number][] }[];
}

export interface CaptureRow {
  id: number;
  kind: "usage" | "quota" | "backfill";
  source: string;
  provider: string | null;
  started_at: string;
  finished_at: string | null;
  status: "running" | "ok" | "error";
  tool_version: string | null;
  stats: string | null;
  error: string | null;
}

export interface RevisionRow {
  id: number;
  kind: "changed" | "missing" | "replaced";
  day: string;
  model: string;
  provider: string;
  source: string;
  observed_at: string;
  observed_total: number | null;
  ledger_total: number | null;
}

export interface Status {
  machine: { id: string; label: string };
  timezone: string;
  grace_days: number;
  range: { start: string; end: string } | null;
  ledger_rows: number;
  open_rows: number;
  final_rows: number;
  revisions: number;
  replacements: number;
  quota_samples: number;
  last_ok: { source: string; provider: string | null; kind: string; at: string }[];
  recent_failures: { source: string; started_at: string; error: string }[];
  version: string;
  today: string;
}

export interface CaptureOutcome {
  source: string;
  ok: boolean;
  error: string | null;
}
