import { useEffect, useRef } from "react";
import { echarts, type EChartsOption } from "./echarts";

interface Props {
  option: EChartsOption;
  height: number;
  // Changing this key rebuilds the chart from scratch (theme switch); data changes animate in place.
  themeKey: string;
  ariaLabel: string;
}

export function Chart({ option, height, themeKey, ariaLabel }: Props) {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!el.current) return;
    const instance = echarts.init(el.current, undefined, { renderer: "canvas" });
    chart.current = instance;
    const observer = new ResizeObserver(() => instance.resize({ animation: { duration: 200 } }));
    observer.observe(el.current);
    return () => {
      observer.disconnect();
      instance.dispose();
      chart.current = null;
    };
  }, [themeKey]);

  useEffect(() => {
    // replaceMerge keeps shared components so data changes animate, while dropped series disappear.
    chart.current?.setOption(option, { replaceMerge: ["series", "visualMap", "calendar", "dataZoom"], lazyUpdate: true });
  }, [option, themeKey]);

  return <div ref={el} style={{ height, width: "100%" }} role="img" aria-label={ariaLabel} />;
}
