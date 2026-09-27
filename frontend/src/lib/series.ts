import type { Daily, DayValue } from "../api/types";

export const PROVIDER_ORDER = ["claude", "codex", "antigravity"];

export function orderedProviders(daily: Daily): Daily["providers"] {
  const rank = (p: string) => {
    const i = PROVIDER_ORDER.indexOf(p);
    return i === -1 ? PROVIDER_ORDER.length : i;
  };
  return [...daily.providers].sort((a, b) => rank(a.provider) - rank(b.provider));
}

export type Metric = "tokens" | "cost";

export function metricValue(v: DayValue | null, metric: Metric): number {
  if (!v) return 0;
  return metric === "tokens" ? v.tokens : (v.cost_usd ?? 0);
}

// Values at or above each threshold get the next colour step; zero stays at step 0.
export function quantileThresholds(values: number[], steps = 4): number[] {
  const nonZero = values.filter((v) => v > 0).sort((a, b) => a - b);
  if (nonZero.length === 0) return [1, 2, 3, 4].slice(0, steps);
  const out: number[] = [];
  for (let i = 0; i < steps; i++) {
    const q = nonZero[Math.min(nonZero.length - 1, Math.floor((i / steps) * nonZero.length))];
    out.push(i === 0 ? Math.min(q, nonZero[0]) : Math.max(q, out[i - 1] * 1.0001));
  }
  return out;
}

export function movingAverage(values: number[], window: number): (number | null)[] {
  return values.map((_, i) => {
    if (i < window - 1) return null;
    let sum = 0;
    for (let j = i - window + 1; j <= i; j++) sum += values[j];
    return sum / window;
  });
}
