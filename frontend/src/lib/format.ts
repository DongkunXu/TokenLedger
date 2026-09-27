import type { Lang } from "../i18n/strings";

// Compact token counts: Chinese uses 万 / 亿, English uses K / M / B.
export function tokens(n: number | null | undefined, lang: Lang, digits = 1): string {
  if (n == null) return "—";
  const abs = Math.abs(n);
  if (lang === "zh") {
    if (abs >= 1e8) return `${trim((n / 1e8).toFixed(digits))}亿`;
    if (abs >= 1e4) return `${trim((n / 1e4).toFixed(digits))}万`;
    return Math.round(n).toLocaleString("zh-CN");
  }
  if (abs >= 1e9) return `${trim((n / 1e9).toFixed(digits))}B`;
  if (abs >= 1e6) return `${trim((n / 1e6).toFixed(digits))}M`;
  if (abs >= 1e3) return `${trim((n / 1e3).toFixed(digits))}K`;
  return Math.round(n).toLocaleString("en-US");
}

export function usd(n: number | null | undefined, digits?: number): string {
  if (n == null) return "—";
  const d = digits ?? (Math.abs(n) >= 1000 ? 0 : Math.abs(n) >= 10 ? 1 : 2);
  return `$${n.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d })}`;
}

export function percent(n: number | null | undefined, digits = 0): string {
  return n == null ? "—" : `${(n * 100).toFixed(digits)}%`;
}

export function shortDate(day: string, lang: Lang): string {
  const [, m, d] = day.split("-").map(Number);
  return lang === "zh" ? `${m}月${d}日` : new Date(`${day}T12:00:00`).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}

export function relativeTime(iso: string, lang: Lang): string {
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  const rtf = new Intl.RelativeTimeFormat(lang === "zh" ? "zh-CN" : "en", { numeric: "auto" });
  if (diff < 60) return rtf.format(-Math.round(diff), "second");
  if (diff < 3600) return rtf.format(-Math.round(diff / 60), "minute");
  if (diff < 86400) return rtf.format(-Math.round(diff / 3600), "hour");
  return rtf.format(-Math.round(diff / 86400), "day");
}

function trim(s: string): string {
  return s.includes(".") ? s.replace(/\.?0+$/, "") : s;
}
