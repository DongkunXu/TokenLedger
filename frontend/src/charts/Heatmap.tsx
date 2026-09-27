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

const LEFT = 30; // room for weekday labels
const RIGHT = 4;
const TOP = 24; // room for month labels
const BOTTOM = 4;
const YEAR_WEEKS = 53;
const MIN_CELL = 11;
const MAX_CELL = 16;

// Cell size is chosen so a full year fills the card; shorter ranges keep the same square
// cells and simply take fewer columns, so every range looks the same.
function cellFor(width: number): number {
  return Math.max(MIN_CELL, Math.min(MAX_CELL, Math.floor((width - LEFT - RIGHT) / YEAR_WEEKS)));
}

function weekCount(start: string, days: number): number {
  const first = new Date(`${start}T12:00:00`);
  const offset = (first.getDay() + 6) % 7; // Monday-first
  return Math.ceil((days + offset) / 7);
}

interface RenderApi {
  value: (dim: number) => string | number;
  coord: (point: (string | number)[]) => number[];
  visual: (key: string) => string;
}

export function Heatmap({ daily, metric, provider, t, lang, themeKey }: Props) {
  const [ref, width] = useWidth<HTMLDivElement>();

  const { option, height, chartWidth } = useMemo(() => {
    const providers = orderedProviders(daily).filter((p) => provider === "all" || p.provider === provider);
    const values = daily.days.map((_, i) => providers.reduce((sum, p) => sum + metricValue(p.values[i], metric), 0));
    const cell = cellFor(width);
    const weeks = weekCount(daily.start, daily.days.length);
    const gap = Math.max(2, Math.round(cell * 0.16));
    const radius = Math.max(2, Math.round(cell * 0.24));
    const chartWidth = Math.min(width, LEFT + RIGHT + weeks * cell);
    const height = TOP + BOTTOM + 7 * cell;
    const [, t1, t2, t3] = quantileThresholds(values, 4);
    const heat = [0, 1, 2, 3, 4].map((i) => cssVar(`--heat-${i}`));
    const base = baseStyle();
    const fmt = (v: number) => (metric === "tokens" ? fmtTokens(v, lang) : usd(v));

    const option = {
      ...base,
      tooltip: {
        ...base.tooltip,
        formatter: (p: { dataIndex: number }) => {
          const i = p.dataIndex;
          const rows = providers
            .map((pr) => ({ pr, v: pr.values[i] }))
            .filter(({ v }) => v && metricValue(v, metric) > 0)
            .map(({ pr, v }) => tooltipRow(providerColor(pr.provider), providerName(t, pr.provider), fmt(metricValue(v, metric))));
          const open = providers.some((pr) => pr.values[i]?.open);
          const total = rows.length > 1 ? tooltipRow("transparent", t.tooltip.total, fmt(values[i])) : "";
          return tooltipTitle(shortDate(daily.days[i], lang), open ? t.tooltip.open : undefined) + (rows.join("") || fmt(0)) + total;
        },
      },
      visualMap: {
        show: false,
        type: "piecewise",
        dimension: 1,
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
          type: "custom",
          coordinateSystem: "calendar",
          data: daily.days.map((d, i) => [d, values[i]]),
          renderItem: (_params: unknown, api: RenderApi) => {
            const [x, y] = api.coord([api.value(0)]);
            const size = cell - gap;
            return {
              type: "rect",
              shape: { x: x - size / 2, y: y - size / 2, width: size, height: size, r: radius },
              style: { fill: api.visual("color") },
              emphasis: { style: { stroke: cssVar("--text-2"), lineWidth: 1.2 } },
            };
          },
          animationDurationUpdate: 500,
        },
      ],
    };
    return { option, height, chartWidth };
  }, [daily, metric, provider, t, lang, width, themeKey]);

  return (
    <div ref={ref} className="heatmap">
      {width > 0 && (
        <div className="heatmap-canvas" style={{ width: chartWidth }}>
          <Chart option={option} height={height} themeKey={themeKey} ariaLabel={t.heatmap.title} />
        </div>
      )}
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
