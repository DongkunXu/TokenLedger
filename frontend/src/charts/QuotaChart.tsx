import { useMemo } from "react";
import type { QuotaPeaks } from "../api/types";
import { type Lang, type Strings, providerName } from "../i18n/strings";
import { PROVIDER_ORDER } from "../lib/series";
import { cssVar, providerColor } from "../theme/theme";
import { baseStyle, tooltipRow, tooltipTitle } from "./base";
import { echarts } from "./echarts";
import { Chart } from "./Chart";

interface Props {
  peaks: QuotaPeaks;
  window: "weekly" | "session";
  t: Strings;
  lang: Lang;
  themeKey: string;
}

// Account-wide quota use at its highest point: per weekly window, or per day for 5-hour windows.
export function QuotaChart({ peaks, window, t, lang, themeKey }: Props) {
  const option = useMemo(() => {
    const base = baseStyle();
    const weekly = window === "weekly";
    const series = (weekly ? peaks.weekly : peaks.session)
      .filter((s) => PROVIDER_ORDER.includes(s.provider))
      .sort((a, b) => PROVIDER_ORDER.indexOf(a.provider) - PROVIDER_ORDER.indexOf(b.provider));
    const locale = lang === "zh" ? "zh-CN" : "en-GB";
    const dateLabel = (v: string | number) =>
      new Date(typeof v === "string" && v.length === 10 ? `${v}T12:00:00` : v).toLocaleDateString(locale, { month: "short", day: "numeric" });

    return {
      ...base,
      grid: { left: 8, right: 12, top: 36, bottom: 8, containLabel: true },
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
        axisPointer: { type: "line", lineStyle: { color: cssVar("--border-strong") } },
        formatter: (items: { seriesName: string; value: [string, number]; color: string }[]) => {
          if (!items.length) return "";
          const title = `${weekly ? t.quota.weekEnding : t.quota.day} · ${dateLabel(items[0].value[0])}`;
          return tooltipTitle(title) + items.map((it) => tooltipRow(it.color, it.seriesName, `${Math.round(it.value[1])}%`)).join("");
        },
      },
      xAxis: {
        type: "time",
        axisLine: base.axisLine,
        axisTick: { show: false },
        axisLabel: { ...base.axisLabel, hideOverlap: true, formatter: (v: number) => dateLabel(v) },
        splitLine: { show: false },
      },
      yAxis: {
        type: "value",
        max: 100,
        interval: 25,
        axisLabel: { ...base.axisLabel, formatter: "{value}%" },
        splitLine: base.splitLine,
      },
      series: series.map((s) => {
        const color = providerColor(s.provider);
        return {
          name: providerName(t, s.provider),
          type: "line",
          smooth: weekly ? 0.2 : 0.35,
          showSymbol: weekly,
          symbol: "circle",
          symbolSize: 6,
          lineStyle: { width: weekly ? 2 : 1.6, color },
          itemStyle: { color, borderColor: cssVar("--surface"), borderWidth: 1.5 },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: `${color}33` },
              { offset: 1, color: `${color}00` },
            ]),
          },
          emphasis: { focus: "series" },
          data: s.points,
        };
      }),
    };
  }, [peaks, window, t, lang, themeKey]);

  return <Chart option={option} height={240} themeKey={themeKey} ariaLabel={t.quota.title} />;
}
