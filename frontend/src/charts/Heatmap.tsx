import { useMemo } from "react";
import type { Daily } from "../api/types";
import { type Lang, type Strings, providerName } from "../i18n/strings";
import { shortDate, tokens as fmtTokens, usd } from "../lib/format";
import { type Metric, metricValue, orderedProviders, quantileThresholds } from "../lib/series";
import { useWidth } from "../lib/useWidth";
import { cssVar, providerColor } from "../theme/theme";
import { baseStyle, tooltipRow, tooltipTitle } from "./base";
import { Chart } from "./Chart";

interface Props {
  daily: Daily;
  metric: Metric;
  provider: string; // "all" or a provider id
  t: Strings;
  lang: Lang;
  themeKey: string;
}

const LEFT = 30;
const TOP = 26;

export function Heatmap({ daily, metric, provider, t, lang, themeKey }: Props) {
  const [ref, width] = useWidth<HTMLDivElement>();

  const { option, height } = useMemo(() => {
    const providers = orderedProviders(daily).filter((p) => provider === "all" || p.provider === provider);
    const values = daily.days.map((_, i) => providers.reduce((sum, p) => sum + metricValue(p.values[i], metric), 0));
    const first = new Date(`${daily.start}T12:00:00`);
    const offset = (first.getDay() + 6) % 7; // Monday-first
    const weeks = Math.ceil((daily.days.length + offset) / 7);
    const cell = Math.max(9, Math.min(20, Math.floor((width - LEFT - 8) / Math.max(weeks, 1))));
    const h = TOP + cell * 7 + 8;
    const [, t1, t2, t3] = quantileThresholds(values, 4);
    const heat = [0, 1, 2, 3, 4].map((i) => cssVar(`--heat-${i}`));
    const base = baseStyle();
    const fmt = (v: number) => (metric === "tokens" ? fmtTokens(v, lang) : usd(v));

    const option = {
      ...base,
      tooltip: {
        ...base.tooltip,
        formatter: (p: { data: [string, number]; dataIndex: number }) => {
          const i = p.dataIndex;
          const day = daily.days[i];
          const rows = providers
            .map((pr) => ({ pr, v: pr.values[i] }))
            .filter(({ v }) => v && (metric === "tokens" ? v.tokens > 0 : (v.cost_usd ?? 0) > 0))
            .map(({ pr, v }) => tooltipRow(providerColor(pr.provider), providerName(t, pr.provider), fmt(metricValue(v, metric))));
          const open = providers.some((pr) => pr.values[i]?.open);
          const total = rows.length > 1 ? tooltipRow("transparent", t.tooltip.total, fmt(values[i])) : "";
          return tooltipTitle(shortDate(day, lang), open ? t.tooltip.open : undefined) + (rows.join("") || fmt(0)) + total;
        },
      },
      visualMap: {
        show: false,
        type: "piecewise",
        pieces: [
          { max: 0, color: heat[0] },
          { gt: 0, lt: t1, color: heat[1] },
          { gte: t1, lt: t2, color: heat[2] },
          { gte: t2, lt: t3, color: heat[3] },
          { gte: t3, color: heat[4] },
        ],
      },
      calendar: {
        range: [daily.start, daily.end],
        left: LEFT,
        right: 8,
        top: TOP,
        cellSize: [cell, cell],
        firstDay: 1,
        splitLine: { show: false },
        itemStyle: { color: "transparent", borderWidth: 0 },
        yearLabel: { show: false },
        dayLabel: {
          firstDay: 1,
          nameMap: lang === "zh" ? ["", "一", "", "三", "", "五", ""] : ["", "Mon", "", "Wed", "", "Fri", ""],
          color: cssVar("--text-3"),
          fontSize: 10,
          margin: 8,
        },
        monthLabel: {
          color: cssVar("--text-3"),
          fontSize: 11,
          margin: 8,
          ...(lang === "zh" ? { formatter: "{M}月" } : { nameMap: "EN" }),
        },
      },
      series: [
        {
          type: "heatmap",
          coordinateSystem: "calendar",
          data: daily.days.map((d, i) => [d, values[i]]),
          itemStyle: { borderColor: cssVar("--surface"), borderWidth: Math.max(2, Math.round(cell / 6)), borderRadius: Math.round(cell / 4) },
          emphasis: { itemStyle: { borderColor: cssVar("--text-2"), borderWidth: 1.5 } },
          animationDelay: (i: number) => i * 2,
        },
      ],
    };
    return { option, height: h };
  }, [daily, metric, provider, t, lang, width, themeKey]);

  return (
    <div ref={ref} className="heatmap">
      {width > 0 && <Chart option={option} height={height} themeKey={themeKey} ariaLabel={t.heatmap.title} />}
      <div className="heat-legend">
        <span>{t.heatmap.less}</span>
        {[0, 1, 2, 3, 4].map((i) => (
          <i key={i} style={{ background: `var(--heat-${i})` }} />
        ))}
        <span>{t.heatmap.more}</span>
      </div>
    </div>
  );
}
