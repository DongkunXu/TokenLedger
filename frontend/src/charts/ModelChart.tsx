import { useMemo } from "react";
import type { ModelRow } from "../api/types";
import { type Lang, type Strings, providerName } from "../i18n/strings";
import { tokens as fmtTokens, usd } from "../lib/format";
import { cssVar, providerColor } from "../theme/theme";
import { baseStyle, tooltipRow, tooltipTitle } from "./base";
import { Chart } from "./Chart";

interface Props {
  models: ModelRow[];
  by: "tokens" | "cost";
  t: Strings;
  lang: Lang;
  themeKey: string;
}

const TOP_N = 10;

export function ModelChart({ models, by, t, lang, themeKey }: Props) {
  const { option, height } = useMemo(() => {
    const base = baseStyle();
    const value = (m: ModelRow) => (by === "tokens" ? m.tokens : (m.cost_usd ?? 0));
    const rows = models
      .filter((m) => by === "tokens" || m.cost_usd != null)
      .sort((a, b) => value(b) - value(a))
      .slice(0, TOP_N)
      .reverse();
    const fmt = (v: number) => (by === "tokens" ? fmtTokens(v, lang) : usd(v));
    const option = {
      ...base,
      grid: { left: 4, right: 64, top: 4, bottom: 4, containLabel: true },
      tooltip: {
        ...base.tooltip,
        trigger: "item",
        formatter: (p: { dataIndex: number }) => {
          const m = rows[p.dataIndex];
          return (
            tooltipTitle(m.model, providerName(t, m.provider)) +
            tooltipRow(providerColor(m.provider), "Token", fmtTokens(m.tokens, lang)) +
            tooltipRow(providerColor(m.provider), t.kpi.cost, m.cost_usd == null ? t.models.unpriced : usd(m.cost_usd))
          );
        },
      },
      xAxis: { type: "value", show: false },
      yAxis: {
        type: "category",
        data: rows.map((m) => m.model),
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { ...base.axisLabel, color: cssVar("--text-2"), fontSize: 12, margin: 12 },
      },
      series: [
        {
          type: "bar",
          barWidth: 12,
          showBackground: true,
          backgroundStyle: { color: cssVar("--surface-2"), borderRadius: 6 },
          itemStyle: { borderRadius: 6 },
          label: {
            show: true,
            position: "right",
            color: cssVar("--text-2"),
            fontSize: 11,
            formatter: (p: { value: number }) => fmt(p.value),
          },
          data: rows.map((m) => ({ value: value(m), itemStyle: { color: providerColor(m.provider) } })),
          animationDelay: (i: number) => (rows.length - i) * 40,
        },
      ],
    };
    return { option, height: Math.max(160, rows.length * 30 + 12) };
  }, [models, by, t, lang, themeKey]);

  return <Chart option={option} height={height} themeKey={themeKey} ariaLabel={t.models.title} />;
}
