import type { Lang } from "../i18n/strings";

// Compact token counts: Chinese uses 万 / 亿, English uses K / M / B.
export function tokens(n: number | null | undefined, lang: Lang, digits = 1): string {
  if (n == null) return "—";
  const [value, unit] = tokenParts(n, lang, digits);
  return value + unit;
}

// A token count split into number and unit, for layouts that set the unit smaller.
export function tokenParts(n: number, lang: Lang, digits = 2): [string, string] {
  const abs = Math.abs(n);
  const f = (v: number, d: number) => trim(v.toFixed(d));
  if (lang === "zh") {
    if (abs >= 1e8) return [f(n / 1e8, digits), "亿"];
    if (abs >= 1e4) return [f(n / 1e4, 1), "万"];
    return [Math.round(n).toLocaleString("zh-CN"), ""];
  }
  if (abs >= 1e9) return [f(n / 1e9, digits), "B"];
  if (abs >= 1e6) return [f(n / 1e6, 1), "M"];
  if (abs >= 1e3) return [f(n / 1e3, 1), "K"];
  return [Math.round(n).toLocaleString("en-US"), ""];
}

export function usd(n: number | null | undefined, digits?: number): string {
  if (n == null) return "—";
  const d = digits ?? (Math.abs(n) >= 1000 ? 0 : Math.abs(n) >= 10 ? 1 : 2);
  return `$${n.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d })}`;
}

export function percent(n: number | null | undefined, digits = 0): string {
  return n == null ? "—" : `${(n * 100).toFixed(digits)}%`;
}

// "2026-09-29" -> "09.29"
export function dotDate(day: string): string {
  return day.slice(5).replace("-", ".");
}

export const MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];
export const WEEKDAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"];

export function monthCode(day: string): string {
  return MONTHS[Number(day.slice(5, 7)) - 1];
}

// Monday = 0 … Sunday = 6
export function weekday(day: string): number {
  return (new Date(`${day}T12:00:00Z`).getUTCDay() + 6) % 7;
}

export function hhmm(d: Date): string {
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

export function relativeTime(iso: string, lang: Lang): string {
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  const rtf = new Intl.RelativeTimeFormat(lang === "zh" ? "zh-CN" : "en", { numeric: "auto" });
  if (diff < 60) return rtf.format(-Math.round(diff), "second");
  if (diff < 3600) return rtf.format(-Math.round(diff / 60), "minute");
  if (diff < 86400) return rtf.format(-Math.round(diff / 3600), "hour");
  return rtf.format(-Math.round(diff / 86400), "day");
}

// A round axis step that splits `max` into about `n` parts.
export function niceStep(max: number, n = 4): number {
  if (max <= 0) return 1;
  const raw = max / n;
  const p = 10 ** Math.floor(Math.log10(raw));
  const f = raw / p;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * p;
}

function trim(s: string): string {
  return s.includes(".") ? s.replace(/\.?0+$/, "") : s;
}
