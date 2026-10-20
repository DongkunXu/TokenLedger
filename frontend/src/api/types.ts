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

// Latest reading of one limit window (GET /api/quota/now).
export interface QuotaReading {
  provider: string;
  window: string; // "weekly", "session", ...
  window_minutes: number | null;
  captured_at: string;
  used_percent: number;
  resets_at: string | null;
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

export interface SourceHealth {
  machine: string | null; // which machine's source (several machines push to a hub)
  machine_label: string | null;
  source: string;
  provider: string | null;
  ok: boolean;
  last_at: string; // latest attempt
  last_ok_at: string | null;
  error: string | null;
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
  sources: SourceHealth[];
  machines: MachineInfo[];
  sync: SyncStatus;
  version: string;
  today: string;
  mode?: "local"; // set by the local server; the cloud serves what a machine published
}

// Every machine with data in this ledger (backend views.machines).
export interface MachineInfo {
  id: string;
  label: string;
  os: string;
  first_seen_at: string;
  app_version: string | null;
  snapshot_at: string | null; // another machine: when its data was taken there
  synced_at: string | null; // another machine: when its data arrived here
  last_day: string | null;
  this_machine: boolean;
}

// Whether the machine that made this view is signed in to a cloud ledger (sync/client.status).
export interface SyncStatus {
  signed_in: boolean;
  url?: string;
  user?: string;
  last_attempt_at?: string | null;
  last_ok?: boolean | null;
  last_ok_at?: string | null;
  last_error?: string | null;
  signed_out?: boolean;
  error?: string;
}

// Tokens per machine over a range (backend views.machine_usage). ``other`` is account usage no
// machine in the ledger accounts for: other devices, cloud tasks.
export interface MachineUsageRow {
  id: string;
  label: string;
  os: string | null;
  this_machine: boolean;
  tokens: number;
  cost_usd: number | null;
  providers: Record<string, number>;
  values: number[];
}

export interface MachineUsage {
  start: string;
  end: string;
  days: string[];
  machines: MachineUsageRow[];
  other: { tokens: number; values: number[]; providers: Record<string, number> };
}

// Everything one dashboard range shows (GET /api/view), locally or from the cloud.
export interface View {
  range: string;
  status: Status;
  summary: Summary;
  daily: Daily;
  models: ModelRow[];
  machines: MachineUsage;
  peaks: QuotaPeaks;
  quota: QuotaReading[];
}

export type Session =
  | { mode: "local" }
  | { mode: "cloud"; user: { name: string; admin: boolean } | null; setup_needed: boolean; google: boolean; invites: boolean };

export interface Identity {
  provider: string;
  email: string | null;
  created_at: string;
}

export interface Device {
  id: string;
  machine: string;
  label: string;
  os: string;
  app_version: string | null;
  created_at: string;
  last_seen_at: string | null;
  taken_at: string | null;
}

export interface PasskeyInfo {
  id: string;
  created_at: string;
  last_used_at: string | null;
}

export interface LinkRequest {
  machine: { id: string; label: string; os: string };
  app_version: string;
  from: string;
  requested_at: string;
  replaces: { label: string; last_seen_at: string | null } | null;
}

export interface CaptureOutcome {
  source: string;
  ok: boolean;
  error: string | null;
}
