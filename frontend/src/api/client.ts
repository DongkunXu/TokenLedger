import type { CaptureOutcome, CaptureRow, Daily, ModelRow, QuotaPeaks, RevisionRow, Status, Summary } from "./types";

export interface Span {
  start?: string;
  end?: string;
}

async function get<T>(path: string, params: Record<string, string | number | undefined> = {}): Promise<T> {
  const query = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined) query.set(k, String(v));
  const res = await fetch(`/api/${path}${query.size ? `?${query}` : ""}`);
  if (!res.ok) throw new Error(`${path}: ${res.status} ${await res.text()}`);
  return res.json() as Promise<T>;
}

export const api = {
  status: () => get<Status>("status"),
  summary: (s: Span) => get<Summary>("summary", { ...s }),
  daily: (s: Span) => get<Daily>("daily", { ...s }),
  models: (s: Span) => get<ModelRow[]>("models", { ...s }),
  quotaPeaks: (s: Span) => get<QuotaPeaks>("quota/peaks", { ...s }),
  captures: (limit = 20) => get<CaptureRow[]>("captures", { limit }),
  revisions: (limit = 20) => get<RevisionRow[]>("revisions", { limit }),
  capture: async (): Promise<CaptureOutcome[]> => {
    const res = await fetch("/api/capture", { method: "POST" });
    if (!res.ok) throw new Error(`capture: ${res.status} ${await res.text()}`);
    return res.json();
  },
};
