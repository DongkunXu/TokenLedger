import { useMemo, useState } from "react";
import type { Daily } from "../api/types";
import { DayTip } from "../components/DayTip";
import { Segs } from "../components/Meters";
import { Panel } from "../components/Panel";
import { Toggle } from "../components/Toggle";
import { useTooltip } from "../components/Tooltip";
import { type Lang, type Strings, providerCode } from "../i18n/strings";
import { MONTHS, WEEKDAYS, monthCode, tokens, usd, weekday } from "../lib/format";
import { type Metric, dayTotals, heatLevels, orderedProviders, providerSeries, sum } from "../lib/series";
import { useSize } from "../lib/useSize";

// 03 · a calendar of square cells, monthly totals as LED bars that fill the rest of the
// panel, and the average per weekday.
export function ActivityPanel({ daily, t, lang, className }: { daily: Daily; t: Strings; lang: Lang; className: string }) {
  const [provider, setProvider] = useState("all");
  const [metric, setMetric] = useState<Metric>("tokens");
  const series = useMemo(() => providerSeries(daily, metric, provider), [daily, metric, provider]);
  const values = useMemo(() => dayTotals(series, daily.days.length), [series, daily.days.length]);
  const fmt = (v: number, d = 1) => (metric === "cost" ? usd(v, 0) : tokens(v, lang, d));
  const providers = orderedProviders(daily).map((p) => p.provider);

  return (
    <Panel
      id="s03"
      index="03"
      code="AC"
      title="Activity"
      sub={t.panels.activity}
      className={`activity ${className}`}
      meta={
        <>
          <Toggle label="provider" value={provider} onChange={setProvider} options={[{ value: "all", label: "All" }, ...providers.map((p) => ({ value: p, label: providerCode(p) }))]} />
          <Toggle label="metric" value={metric} onChange={setMetric} options={[{ value: "tokens", label: "Tok" }, { value: "cost", label: "$" }]} />
        </>
      }
    >
      <div className="activity-body">
        <div className="activity-main">
          <Calendar daily={daily} values={values} t={t} lang={lang} />
          <div className="heat-foot">
            <PeakNote daily={daily} values={values} fmt={fmt} t={t} />
            <span className="ramp">
              <span className="lab">{t.activity.less}</span>
              {[0, 1, 2, 3, 4].map((l) => (
                <i key={l} style={{ background: `var(--h${l})` }} />
              ))}
              <span className="lab">{t.activity.more}</span>
            </span>
          </div>
          <Monthly daily={daily} series={series} fmt={fmt} t={t} />
        </div>
        <Weekdays daily={daily} values={values} fmt={fmt} t={t} />
      </div>
    </Panel>
  );
}

function PeakNote({ daily, values, fmt, t }: { daily: Daily; values: number[]; fmt: (v: number, d?: number) => string; t: Strings }) {
  const max = Math.max(...values);
  const i = values.indexOf(max);
  return (
    <span className="peak-note">
      <span className="lab">{t.activity.peak}</span> <span className="mono">{max > 0 ? `${fmt(max, 2)} · ${daily.days[i]}` : "—"}</span>{" "}
      <span className="lab">{t.activity.active}</span> <span className="mono">{values.filter((v) => v > 0).length} D</span>
    </span>
  );
}

const LEFT = 32; // weekday labels
const TOP = 16; // month labels

function Calendar({ daily, values, t, lang }: { daily: Daily; values: number[]; t: Strings; lang: Lang }) {
  const [ref, { width }] = useSize<HTMLDivElement>();
  const tip = useTooltip();
  const offset = daily.days.length ? weekday(daily.days[0]) : 0;
  const cols = Math.ceil((offset + daily.days.length) / 7);
  const pitch = width ? Math.max(8, Math.min(26, Math.floor((width - LEFT) / cols))) : 12;
  const gap = pitch >= 14 ? 3 : 2;
  const cell = pitch - gap;
  const levels = heatLevels(values);
  let lastMonth = "";

  return (
    <div className="calendar" ref={ref}>
      <svg width={LEFT + cols * pitch} height={TOP + 7 * pitch} role="img" aria-label="daily activity" onMouseLeave={tip.hide}>
        {daily.days.map((d, i) => {
          const k = offset + i;
          const x = LEFT + Math.floor(k / 7) * pitch;
          const y = TOP + (k % 7) * pitch;
          const month = d.slice(0, 7);
          const label = month !== lastMonth && Number(d.slice(8)) <= 7 && k % 7 === 0;
          if (label) lastMonth = month;
          return (
            <g key={d}>
              {label && (
                <text x={x} y={9}>
                  {monthCode(d)}
                </text>
              )}
              <rect
                className={`c${levels[i]} ${i === daily.days.length - 1 ? "today" : ""}`}
                x={x}
                y={y}
                width={cell}
                height={cell}
                style={{ animationDelay: `${0.2 + Math.floor(k / 7) * 0.02}s` }}
                onMouseMove={(e) => tip.show(e, <DayTip daily={daily} index={i} t={t} lang={lang} />)}
              />
            </g>
          );
        })}
        {[0, 2, 4].map((r) => (
          <text key={r} x={0} y={TOP + r * pitch + cell - 1}>
            {WEEKDAYS[r]}
          </text>
        ))}
      </svg>
    </div>
  );
}

const SLICE = 4;
const SLICE_GAP = 2;

// Monthly totals as bars made of thin slices; the bar colour splits by provider.
function Monthly({ daily, series, fmt, t }: { daily: Daily; series: { provider: string; values: number[] }[]; fmt: (v: number, d?: number) => string; t: Strings }) {
  const [ref, { width, height }] = useSize<HTMLDivElement>();
  const months = useMemo(() => {
    const out: { key: string; v: number[] }[] = [];
    daily.days.forEach((d, i) => {
      const key = d.slice(0, 7);
      if (out[out.length - 1]?.key !== key) out.push({ key, v: series.map(() => 0) });
      series.forEach((s, k) => (out[out.length - 1].v[k] += s.values[i]));
    });
    return out;
  }, [daily, series]);
  const totals = months.map((m) => sum(m.v));
  const max = Math.max(...totals, 1e-9);
  const top = 14;
  const bottom = 16;
  const slots = Math.max(0, Math.floor((height - top - bottom) / (SLICE + SLICE_GAP)));
  const slotW = months.length ? width / months.length : 0;
  const barW = Math.min(64, slotW * 0.62);
  const last = months[months.length - 1];

  return (
    <>
      <div className="sub-head">
        <span className="lab">
          Monthly {t.activity.monthly && <span className="sub">· {t.activity.monthly}</span>}
        </span>
        {months.length > 1 && last && (
          <span className="mono dim">
            {MONTHS[Number(last.key.slice(5)) - 1]} {t.activity.inProgress}
          </span>
        )}
      </div>
      <div className="fill monthly" ref={ref}>
        {width > 0 && height > 0 && (
          <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="monthly totals">
            {months.map((m, i) => {
              const x = i * slotW + (slotW - barW) / 2;
              const lit = Math.round((totals[i] / max) * slots);
              const colors: string[] = [];
              m.v.forEach((v, k) => {
                const n = Math.round((v / (totals[i] || 1)) * lit);
                for (let j = 0; j < n; j++) colors.push(`var(--${series[k].provider}, var(--other))`);
              });
              while (colors.length < lit) colors.push(`var(--${series[0]?.provider ?? "other"}, var(--other))`);
              const yOf = (r: number) => top + (slots - r - 1) * (SLICE + SLICE_GAP) + SLICE_GAP;
              return (
                <g key={m.key}>
                  {Array.from({ length: slots }, (_, r) => (
                    <rect
                      key={r}
                      x={x}
                      y={yOf(r)}
                      width={barW}
                      height={SLICE}
                      className={r < lit ? "lit" : "slot"}
                      fill={r < lit ? colors[r] : undefined}
                      style={r < lit ? { animationDelay: `${0.3 + i * 0.05 + r * 0.01}s` } : undefined}
                    />
                  ))}
                  <text className="val" x={x + barW / 2} y={Math.max(10, yOf(lit - 1) - 4)} textAnchor="middle">
                    {fmt(totals[i])}
                  </text>
                  <text className="ax" x={x + barW / 2} y={height - 2} textAnchor="middle">
                    {MONTHS[Number(m.key.slice(5)) - 1]}
                  </text>
                </g>
              );
            })}
          </svg>
        )}
      </div>
    </>
  );
}

function Weekdays({ daily, values, fmt, t }: { daily: Daily; values: number[]; fmt: (v: number, d?: number) => string; t: Strings }) {
  const totals = [0, 0, 0, 0, 0, 0, 0];
  const counts = [0, 0, 0, 0, 0, 0, 0];
  daily.days.forEach((d, i) => {
    totals[weekday(d)] += values[i];
    counts[weekday(d)] += 1;
  });
  const avg = totals.map((v, i) => v / Math.max(1, counts[i]));
  const max = Math.max(...avg, 1e-9);
  return (
    <div className="weekdays">
      <span className="lab">
        Weekday avg {t.activity.weekday && <span className="sub">· {t.activity.weekday}</span>}
      </span>
      <div className="wk-rows">
        {WEEKDAYS.map((d, i) => (
          <div className="wk" key={d}>
            <span className="lab">{d}</span>
            <Segs fraction={avg[i] / max} n={20} color="var(--ink-2)" delay={0.4} />
            <span className="mono">{fmt(avg[i])}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
