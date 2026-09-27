import { useMemo } from "react";
import type { Daily } from "../api/types";
import { type Lang, type Strings, providerName } from "../i18n/strings";
import { shortDate, tokens as fmtTokens, usd } from "../lib/format";
import { metricValue, movingAverage, orderedProviders } from "../lib/series";
import { cssVar, providerColor } from "../theme/theme";
import { baseStyle, tooltipRow, tooltipTitle } from "./base";
import { echarts } from "./echarts";
import { Chart } from "./Chart";

export type TrendMode = "tokens" | "cost" | "cumulative";

interface Props {
  daily: Daily;
  mode: TrendMode;
  t: Strings;
  lang: Lang;
  themeKey: string;
}

export function TrendChart({ daily, mode, t, lang, themeKey }: Props) {
  const option = useMemo(() => {
    const base = baseStyle();
    const providers = orderedProviders(daily);
    const metric = mode === "tokens" ? "tokens" : "cost";
    const perProvider = providers.map((p) => p.values.map((v) => metricValue(v, metric)));
    const totals = daily.days.map((_, i) => perProvider.reduce((s, arr) => s + arr[i], 0));
    const fmt = (v: number) => (metric === "tokens" ? fmtTokens(v, lang) : usd(v));
    const cumulative = mode === "cumulative";

    const series: object[] = providers.map((p, k) => {
      const color = providerColor(p.provider);
      if (cumulative) {
        let run = 0;
        return {
          name: providerName(t, p.provider),
          type: "line",
          stack: "cum",
          smooth: 0.25,
          showSymbol: false,
          lineStyle: { width: 2, color },
          itemStyle: { color },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: `${color}55` },
              { offset: 1, color: `${color}08` },
            ]),
          },
          data: perProvider[k].map((v) => (run += v)),
        };
      }
      return {
        name: providerName(t, p.provider),
        type: "bar",
        stack: "day",
        barMaxWidth: 14,
        itemStyle: {
          color,
          borderRadius: k === providers.length - 1 ? [3, 3, 0, 0] : 0,
        },
        emphasis: { focus: "series" },
        data: perProvider[k].map((v, i) => ({
          value: v,
          itemStyle: p.values[i]?.open ? { opacity: 0.55 } : undefined,
        })),
        animationDelay: (i: number) => i * 3,
      };
    });
    if (!cumulative) {
      series.push({
        name: t.trend.avg7,
        type: "line",
        smooth: 0.4,
        showSymbol: false,
        z: 5,
        lineStyle: { width: 1.5, color: cssVar("--text-2"), type: [4, 3] },
        itemStyle: { color: cssVar("--text-2") },
        data: movingAverage(totals, 7),
      });
    }

    return {
      ...base,
      grid: { left: 8, right: 12, top: 36, bottom: 48, containLabel: true },
      legend: {
        top: 0,
        left: 0,
        icon: "roundRect",
        itemWidth: 10,
        itemHeight: 10,
        itemGap: 16,
        textStyle: { color: cssVar("--text-2"), fontSize: 12 },
      },
      tooltip: {
        ...base.tooltip,
        trigger: "axis",
        axisPointer: { type: cumulative ? "line" : "shadow", shadowStyle: { color: cssVar("--border") }, lineStyle: { color: cssVar("--border-strong") } },
        formatter: (items: { dataIndex: number }[]) => {
          const i = items[0]?.dataIndex ?? 0;
          const open = providers.some((p) => p.values[i]?.open);
          let rows = "";
          let sum = 0;
          providers.forEach((p, k) => {
            const v = cumulative ? perProvider[k].slice(0, i + 1).reduce((a, b) => a + b, 0) : perProvider[k][i];
            sum += v;
            if (v > 0) rows += tooltipRow(providerColor(p.provider), providerName(t, p.provider), fmt(v));
          });
          if (providers.length > 1) rows += tooltipRow("transparent", t.tooltip.total, fmt(sum));
          return tooltipTitle(shortDate(daily.days[i], lang), open ? t.tooltip.open : undefined) + rows;
        },
      },
      xAxis: {
        type: "category",
        data: daily.days,
        axisTick: { show: false },
        axisLine: base.axisLine,
        axisLabel: { ...base.axisLabel, formatter: (d: string) => shortDate(d, lang), hideOverlap: true },
      },
      yAxis: {
        type: "value",
        splitNumber: 4,
        axisLabel: { ...base.axisLabel, formatter: (v: number) => fmt(v) },
        splitLine: base.splitLine,
      },
      dataZoom: [
        // The wheel always scrolls the page: ECharts cancels wheel events whenever wheel zoom is
        // enabled, even when limited to Ctrl. Drag pans; the slider below zooms.
        { type: "inside", throttle: 30, zoomOnMouseWheel: false, moveOnMouseWheel: false, moveOnMouseMove: true },
        {
          type: "slider",
          height: 18,
          bottom: 6,
          borderColor: "transparent",
          backgroundColor: cssVar("--surface-2"),
          fillerColor: cssVar("--bg-glow"),
          dataBackground: { lineStyle: { color: cssVar("--border-strong") }, areaStyle: { color: cssVar("--border") } },
          selectedDataBackground: { lineStyle: { color: cssVar("--codex") }, areaStyle: { color: cssVar("--bg-glow") } },
          handleStyle: { color: cssVar("--surface"), borderColor: cssVar("--border-strong") },
          moveHandleStyle: { color: cssVar("--border-strong") },
          textStyle: { color: cssVar("--text-3"), fontSize: 10 },
          labelFormatter: (_: number, d: string) => shortDate(d, lang),
        },
      ],
      series,
    };
  }, [daily, mode, t, lang, themeKey]);

  return <Chart option={option} height={320} themeKey={themeKey} ariaLabel={t.trend.title} />;
}
