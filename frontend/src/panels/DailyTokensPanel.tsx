import { useMemo, useState } from "react";
import type { Daily } from "../api/types";
import { DayTip } from "../components/DayTip";
import { Panel } from "../components/Panel";
import { useTooltip } from "../components/Tooltip";
import { type Lang, type Strings, providerName } from "../i18n/strings";
import { dotDate, monthCode, niceStep, tokens } from "../lib/format";
import { dayTotals, providerSeries, trailingAverage } from "../lib/series";
import { useSize } from "../lib/useSize";

const PAD = { left: 54, right: 8, top: 22, bottom: 26 };

// 04 · stacked daily columns per provider, the 7-day average in white, the peak labelled.
export function DailyTokensPanel({ daily, t, lang, className }: { daily: Daily; t: Strings; lang: Lang; className: string }) {
  const series = useMemo(() => providerSeries(daily, "tokens"), [daily]);
  return (
    <Panel
      id="s04"
      index="04"
      code="TK"
      title="Daily tokens"
      sub={t.panels.tokens}
      className={`chart-panel ${className}`}
      meta={
        <div className="key">
          {series.map((s) => (
            <span key={s.provider} style={{ ["--c" as string]: `var(--${s.provider}, var(--other))` }}>
              <i />
              {providerName(t, s.provider)}
            </span>
          ))}
          <span style={{ ["--c" as string]: "var(--ink)" }}>
            <i className="ln" />
            {t.chart.avg7}
          </span>
        </div>
      }
    >
      <div className="chart-body">
        <Chart daily={daily} series={series} t={t} lang={lang} />
      </div>
    </Panel>
  );
}

function Chart({ daily, series, t, lang }: { daily: Daily; series: { provider: string; values: number[] }[]; t: Strings; lang: Lang }) {
  const [ref, { width: W, height: H }] = useSize<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const tip = useTooltip();
  const n = daily.days.length;
  const totals = useMemo(() => dayTotals(series, n), [series, n]);
  const avg = useMemo(() => trailingAverage(totals, 7), [totals]);

  const iw = W - PAD.left - PAD.right;
  const ih = H - PAD.top - PAD.bottom;
  const peak = Math.max(...totals, 0);
  const step = niceStep(peak);
  const max = Math.max(step, Math.ceil(peak / step) * step);
  const pitch = n ? iw / n : 0;
  const bw = Math.max(1.5, Math.min(18, pitch - (pitch > 5 ? 2 : 1)));
  const x = (i: number) => PAD.left + (i + 0.5) * pitch;
  const y = (v: number) => PAD.top + ih - (v / max) * ih;
  const pk = totals.indexOf(peak);
  const ticks = Array.from({ length: Math.round(max / step) + 1 }, (_, k) => k * step);

  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const i = Math.floor((e.clientX - r.left - PAD.left) / pitch);
    if (i < 0 || i >= n) {
      setHover(null);
      tip.hide();
      return;
    }
    setHover(i);
    tip.show(e, <DayTip daily={daily} index={i} t={t} lang={lang} />);
  };

  return (
    <div className="fill" ref={ref} style={{ ["--min" as string]: "250px" }}>
      {W > 0 && H > 0 && n > 0 && (
        <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="daily tokens" onPointerMove={onMove} onPointerLeave={() => (setHover(null), tip.hide())}>
          {ticks.map((v) => (
            <g key={v}>
              <line className="gl" x1={PAD.left} x2={W - PAD.right} y1={y(v)} y2={y(v)} />
              <text className="ax" x={PAD.left - 10} y={y(v) + 3} textAnchor="end">
                {v ? tokens(v, lang) : "0"}
              </text>
            </g>
          ))}
          {daily.days.map((d, i) => {
            const first = d.endsWith("-01");
            return (
              <g key={d}>
                {(n <= 120 || first) && <line className="tk" x1={x(i)} x2={x(i)} y1={PAD.top + ih} y2={PAD.top + ih + (first ? 8 : 3)} />}
                {(first || (n <= 60 && i % 7 === 0)) && (
                  <text className="ax" x={x(i) + 3} y={H - 4}>
                    {first ? monthCode(d) : dotDate(d)}
                  </text>
                )}
              </g>
            );
          })}
          {daily.days.map((d, i) => {
            let base = 0;
            const x0 = PAD.left + i * pitch + (pitch - bw) / 2;
            return (
              <g key={d} className={hover != null && hover !== i ? "faded" : undefined}>
                {series.map((s) => {
                  const v = s.values[i];
                  if (!v) return null;
                  const h = (v / max) * ih;
                  const gap = base > 0 && h > 3 ? 1 : 0;
                  const top = y(base + v);
                  base += v;
                  return (
                    <rect
                      key={s.provider}
                      className="grow-y"
                      x={x0}
                      y={top}
                      width={bw}
                      height={Math.max(0.5, h - gap)}
                      fill={`var(--${s.provider}, var(--other))`}
                      style={{ animationDelay: `${(i / n) * 0.5}s` }}
                    />
                  );
                })}
              </g>
            );
          })}
          <path className="avg" d={avg.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("")} />
          {peak > 0 && <PeakLabel x={x(pk)} y={y(peak)} top={PAD.top} right={x(pk) > PAD.left + iw * 0.7} text={`${t.chart.peak} ${tokens(peak, lang, 2)} · ${dotDate(daily.days[pk])}`} />}
          {hover != null && <line className="xh" x1={x(hover)} x2={x(hover)} y1={PAD.top} y2={PAD.top + ih} />}
        </svg>
      )}
    </div>
  );
}

function PeakLabel({ x, y, top, right, text }: { x: number; y: number; top: number; right: boolean; text: string }) {
  return (
    <g className="peak-label">
      <line x1={x} x2={x} y1={y - 4} y2={top - 8} />
      <text x={x + (right ? -6 : 6)} y={top - 10} textAnchor={right ? "end" : "start"}>
        {text}
      </text>
    </g>
  );
}
