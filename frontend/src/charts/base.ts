import { cssVar } from "../theme/theme";

// Shared look for every chart, read from the current theme tokens.
export function baseStyle() {
  const text2 = cssVar("--text-2");
  const text3 = cssVar("--text-3");
  const border = cssVar("--border");
  const font = cssVar("--font");
  return {
    textStyle: { fontFamily: font, color: text2 },
    animationDuration: 900,
    animationDurationUpdate: 600,
    animationEasing: "cubicOut" as const,
    animationEasingUpdate: "cubicInOut" as const,
    tooltip: {
      backgroundColor: cssVar("--surface"),
      borderColor: cssVar("--border-strong"),
      borderWidth: 1,
      padding: [10, 12],
      textStyle: { color: cssVar("--text"), fontFamily: font, fontSize: 12 },
      extraCssText: `box-shadow:${cssVar("--shadow")};border-radius:10px;`,
    },
    axisLabel: { color: text3, fontSize: 11, fontFamily: font },
    splitLine: { lineStyle: { color: border, type: [3, 4] as number[] } },
    axisLine: { lineStyle: { color: border } },
  };
}

export function tooltipRow(color: string, label: string, value: string, muted = false): string {
  const c = muted ? cssVar("--text-3") : cssVar("--text");
  return (
    `<div style="display:flex;align-items:center;gap:8px;justify-content:space-between;min-width:180px;line-height:20px">` +
    `<span style="display:flex;align-items:center;gap:6px;color:${cssVar("--text-2")}">` +
    `<i style="width:8px;height:8px;border-radius:2px;background:${color};display:inline-block"></i>${label}</span>` +
    `<b style="font-weight:600;color:${c};font-variant-numeric:tabular-nums">${value}</b></div>`
  );
}

export function tooltipTitle(text: string, note?: string): string {
  const n = note ? `<span style="color:${cssVar("--text-3")};font-weight:400;margin-left:6px">${note}</span>` : "";
  return `<div style="font-weight:600;margin-bottom:6px;color:${cssVar("--text")}">${text}${n}</div>`;
}
