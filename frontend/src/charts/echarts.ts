// Only the ECharts pieces the dashboard uses, to keep the bundle small.
import { BarChart, HeatmapChart, LineChart } from "echarts/charts";
import {
  CalendarComponent,
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  TooltipComponent,
  VisualMapComponent,
} from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";

echarts.use([
  BarChart,
  LineChart,
  HeatmapChart,
  CalendarComponent,
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  TooltipComponent,
  VisualMapComponent,
  CanvasRenderer,
]);

export { echarts };
export type EChartsOption = echarts.EChartsCoreOption;
