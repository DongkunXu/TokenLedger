import { useMemo } from "react";
import type { Daily } from "../api/types";
import { Panel } from "../components/Panel";
import { TipRow, useTooltip } from "../components/Tooltip";
import { type Strings, providerName } from "../i18n/strings";
import { monthCode, usd, weekday } from "../lib/format";
import { providerSeries, sum } from "../lib/series";
import { useSize } from "../lib/useSize";

const UNITS = [1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000];
const AXIS = 18;
const HEADROOM = 10;

interface Week {
  start: string;
  end: string;
  v: number[]; // cost per provider
}

// 05 · weekly cost as a block matrix: one column per week, one lit block per unit of
// money. Columns fit the width, rows fit the height, and the unit follows from both.
export function WeeklyCostPanel({ daily, t, className }: { daily: Daily; t: Strings; className: string }) {
  const series = useMemo(() => providerSeries(daily, "cost").filter((s) => s.values.some((v) => v > 0)), [daily]);
  const weeks = useMemo(() => {
    const out: Week[] = [];
    daily.days.forEach((d, i) => {
      if (!out.length || weekday(d) === 0) out.push({ start: d, end: d, v: series.map(() => 0) });
      const w = out[out.length - 1];
      w.end = d;
      series.forEach((s, k) => (w.v[k] += s.values[i]));
    });
    return out;
  }, [daily, series]);
  const totals = weeks.map((w) => sum(w.v));
  const done = totals.slice(0, -1);
  const [ref, size] = useSize<HTMLDivElement>();
  const layout = matrixLayout(weeks.length, Math.max(...totals, 0), size);

  return (
    <Panel id="s05" index="05" code="$$" title="Weekly cost" sub={t.panels.cost} className={`chart-panel ${className}`}>
      <div className="readbar">
        <Read code="Now" sub={t.cost.now} value={usd(totals[totals.length - 1] ?? 0, 0)} />
        <Read code="Prev" sub={t.cost.prev} value={usd(totals[totals.length - 2] ?? 0, 0)} />
        <Read code="Avg" sub={t.cost.avg} value={usd(done.length ? sum(done) / done.length : 0, 0)} />
        <Read code="Block" sub={t.cost.block} value={layout ? `= ${usd(layout.unit, 0)}` : ""} small />
      </div>
      <div className="chart-body">
        <div className="fill" ref={ref} style={{ ["--min" as string]: "180px" }}>
          {layout && <Matrix weeks={weeks} totals={totals} providers={series.map((s) => s.provider)} layout={layout} t={t} />}
        </div>
        <div className="key cost-key">
          {series.map((s) => (
            <span key={s.provider} style={{ ["--c" as string]: `var(--${s.provider}, var(--other))` }}>
              <i />
              {providerName(t, s.provider)}
            </span>
          ))}
          <span className="dim push">{t.cost.dashed}</span>
        </div>
      </div>
    </Panel>
  );
}

function Read({ code, sub, value, small = false }: { code: string; sub: string; value: string; small?: boolean }) {
  return (
    <div>
      <span className="lab">{code}</span>
      {sub && <span className="sub">{sub}</span>}
      <div className={`v ${small ? "small" : "num"}`}>{value}</div>
    </div>
  );
}

interface Layout {
  width: number;
  height: number;
  pitch: number; // cell + gap
  cell: number;
  rows: number;
  unit: number; // dollars per lit block
}

function matrixLayout(weekCount: number, maxWeek: number, { width, height }: { width: number; height: number }): Layout | null {
  if (!weekCount || !width || !height) return null;
  const pitch = Math.max(5, Math.min(22, Math.floor(width / weekCount)));
  const rows = Math.max(8, Math.floor((height - AXIS - HEADROOM) / pitch));
  const unit = UNITS.find((u) => maxWeek / rows <= u) ?? UNITS[UNITS.length - 1];
  return { width, height, pitch, cell: pitch - (pitch >= 10 ? 2 : 1), rows, unit };
}

// Lit blocks per provider for one week: the week's total decides how many, and the
// largest remainders decide whose, so small shares never make blocks disappear.
function blocks(values: number[], unit: number): number[] {
  const exact = values.map((v) => v / unit);
  const counts = exact.map(Math.floor);
  let left = Math.round(sum(values) / unit) - sum(counts);
  const byRemainder = exact.map((x, k) => [x - Math.floor(x), k]).sort((a, b) => b[0] - a[0]);
  for (const [, k] of byRemainder) {
    if (left-- <= 0) break;
    counts[k] += 1;
  }
  return counts;
}

function Matrix({ weeks, totals, providers, layout, t }: { weeks: Week[]; totals: number[]; providers: string[]; layout: Layout; t: Strings }) {
  const tip = useTooltip();
  const { width: W, height: H, pitch, cell, rows, unit } = layout;
  const x0 = W - weeks.length * pitch; // latest week at the right edge
  const gridH = rows * pitch;
  let lastMonth = "";

  return (
    <svg viewBox={`0 ${-HEADROOM} ${W} ${H}`} role="img" aria-label="weekly cost" onMouseLeave={tip.hide}>
      {weeks.map((w, i) => {
        const colors = blocks(w.v, unit).flatMap((n, k) => Array<string>(n).fill(`var(--${providers[k]}, var(--other))`));
        const month = w.start.slice(0, 7);
        const label = month !== lastMonth && Number(w.start.slice(8)) <= 7;
        if (label) lastMonth = month;
        const x = x0 + i * pitch;
        return (
          <g
            key={w.start}
            onMouseMove={(e) =>
              tip.show(
                e,
                <>
                  <div className="tip-head pix">
                    {t.tooltip.week} {w.start} → {w.end}
                  </div>
                  {providers.map((p, k) => (
                    <TipRow key={p} color={`var(--${p}, var(--other))`} label={providerName(t, p)} value={usd(w.v[k])} />
                  ))}
                  <TipRow total label={t.tooltip.total} value={usd(totals[i])} />
                </>,
              )
            }
          >
            {Array.from({ length: rows }, (_, r) => (
              <rect
                key={r}
                x={x}
                y={gridH - (r + 1) * pitch}
                width={cell}
                height={cell}
                className={r < colors.length ? "lit" : "slot"}
                fill={r < colors.length ? colors[r] : undefined}
                style={r < colors.length ? { animationDelay: `${0.3 + i * 0.02 + r * 0.015}s` } : undefined}
              />
            ))}
            {colors.length > rows && (
              <text className="ax" x={x + cell / 2} y={-2} textAnchor="middle">
                +
              </text>
            )}
            {label && (
              <text className="ax" x={x} y={gridH + 13}>
                {monthCode(w.start)}
              </text>
            )}
            <rect x={x} y={0} width={pitch} height={gridH} fill="transparent" />
          </g>
        );
      })}
      <rect className="cur" x={x0 + (weeks.length - 1) * pitch - 1.5} y={-1.5} width={cell + 3} height={gridH + 1} />
    </svg>
  );
}
