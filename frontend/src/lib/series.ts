import type { Daily, DayValue } from "../api/types";

export const PROVIDER_ORDER = ["claude", "codex", "antigravity"];

function rank(p: string): number {
  const i = PROVIDER_ORDER.indexOf(p);
  return i === -1 ? PROVIDER_ORDER.length : i;
}

export function byProviderOrder<T>(items: T[], key: (x: T) => string): T[] {
  return [...items].sort((a, b) => rank(key(a)) - rank(key(b)));
}

export function orderedProviders(daily: Daily): Daily["providers"] {
  return byProviderOrder(daily.providers, (p) => p.provider);
}

export type Metric = "tokens" | "cost";

export function metricValue(v: DayValue | null, metric: Metric): number {
  if (!v) return 0;
  return metric === "tokens" ? v.tokens : (v.cost_usd ?? 0);
}

// Per-day values of one metric for each provider, in provider order.
export function providerSeries(daily: Daily, metric: Metric, only?: string): { provider: string; values: number[] }[] {
  return orderedProviders(daily)
    .filter((p) => !only || only === "all" || p.provider === only)
    .map((p) => ({ provider: p.provider, values: p.values.map((v) => metricValue(v, metric)) }));
}

export function dayTotals(series: { values: number[] }[], length: number): number[] {
  return Array.from({ length }, (_, i) => series.reduce((s, x) => s + x.values[i], 0));
}

export function sum(values: number[]): number {
  return values.reduce((a, b) => a + b, 0);
}

// Average of the last `window` values up to each day (fewer at the start).
export function trailingAverage(values: number[], window: number): number[] {
  return values.map((_, i) => {
    const part = values.slice(Math.max(0, i - window + 1), i + 1);
    return sum(part) / part.length;
  });
}

// Colour step 0-4 for heatmap cells: 0 for no usage, then the quartiles of the days with usage.
export function heatLevels(values: number[]): number[] {
  const nz = values.filter((v) => v > 0).sort((a, b) => a - b);
  const q = (f: number) => nz[Math.floor(f * (nz.length - 1))] ?? 0;
  const [a, b, c] = [q(0.25), q(0.5), q(0.75)];
  return values.map((v) => (v <= 0 ? 0 : v <= a ? 1 : v <= b ? 2 : v <= c ? 3 : 4));
}
