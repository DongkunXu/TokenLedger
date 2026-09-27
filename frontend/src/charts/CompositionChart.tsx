import { useMemo } from "react";
import type { Summary, TokenParts } from "../api/types";
import { type Lang, type Strings, providerName } from "../i18n/strings";
import { percent, tokens as fmtTokens } from "../lib/format";
import { PROVIDER_ORDER } from "../lib/series";
import { cssVar } from "../theme/theme";
import { baseStyle, tooltipRow, tooltipTitle } from "./base";
import { Chart } from "./Chart";

interface Props {
  summary: Summary;
  t: Strings;
  lang: Lang;
  themeKey: string;
}

type Part = Exclude<keyof TokenParts, "reasoning">;
const PARTS: Part[] = ["cache_read", "cache_write", "input_uncached", "output"];
const COLOR_VAR: Record<Part, string> = {
  cache_read: "--part-cache-read",
  cache_write: "--part-cache-write",
  input_uncached: "--part-input",
  output: "--part-output",
};

// 100% stacked bars: what each provider's tokens are made of (local detail only).
export function CompositionChart({ summary, t, lang, themeKey }: Props) {
  const option = useMemo(() => {
    const base = baseStyle();
    const providers = PROVIDER_ORDER.filter((p) => {
      const parts = summary.providers[p]?.parts ?? {};
      return PARTS.some((k) => (parts[k] ?? 0) > 0);
    }).reverse();
    const totals = providers.map((p) => PARTS.reduce((s, k) => s + (summary.providers[p].parts[k] ?? 0), 0));
    return {
      ...base,
      grid: { left: 4, right: 8, top: 34, bottom: 4, containLabel: true },
      legend: {
        top: 0,
        left: 0,
        icon: "roundRect",
        itemWidth: 10,
        itemHeight: 10,
        itemGap: 14,
        textStyle: { color: cssVar("--text-2"), fontSize: 12 },
      },
      tooltip: {
        ...base.tooltip,
        trigger: "axis",
        axisPointer: { type: "none" },
        formatter: (items: { dataIndex: number }[]) => {
          const i = items[0].dataIndex;
          const p = providers[i];
          const parts = summary.providers[p].parts;
          return (
            tooltipTitle(providerName(t, p)) +
            PARTS.map((k) =>
              tooltipRow(cssVar(COLOR_VAR[k]), t.composition[k], `${fmtTokens(parts[k] ?? 0, lang)} · ${percent((parts[k] ?? 0) / totals[i], 1)}`),
            ).join("")
          );
        },
      },
      xAxis: { type: "value", max: 1, show: false },
      yAxis: {
        type: "category",
        data: providers.map((p) => providerName(t, p)),
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { ...base.axisLabel, color: cssVar("--text-2"), fontSize: 12, margin: 12 },
      },
      series: PARTS.map((k, idx) => ({
        name: t.composition[k],
        type: "bar",
        stack: "mix",
        barWidth: 16,
        itemStyle: {
          color: cssVar(COLOR_VAR[k]),
          borderRadius: idx === 0 ? [8, 0, 0, 8] : idx === PARTS.length - 1 ? [0, 8, 8, 0] : 0,
        },
        data: providers.map((p, i) => (summary.providers[p].parts[k] ?? 0) / (totals[i] || 1)),
      })),
    };
  }, [summary, t, lang, themeKey]);

  return <Chart option={option} height={150} themeKey={themeKey} ariaLabel={t.composition.title} />;
}
