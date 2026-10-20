import { useMemo, useState } from "react";
import type { MachineUsage } from "../api/types";
import { Segs } from "../components/Meters";
import { Panel } from "../components/Panel";
import { TipRow, useTooltip } from "../components/Tooltip";
import type { Lang, Strings } from "../i18n/strings";
import { dotDate, monthCode, niceStep, percent, tokens, usd } from "../lib/format";
import { useSize } from "../lib/useSize";

const PAD = { left: 54, right: 8, top: 14, bottom: 26 };
// Machines are told apart in greys, so the data colours stay with the providers.
const SHADES = ["var(--h4)", "var(--h3)", "var(--h2)", "var(--h1)"];
const OTHER = "var(--line-3)";

interface Row {
  id: string;
  label: string;
  note: string | null;
  color: string;
  tokens: number;
  cost: number | null;
  values: number[];
}

// 06 · how the range's tokens split across machines: each machine's own logs, plus the
// account usage none of them logged (other devices, cloud tasks).
export function MachinesPanel({ usage, t, lang, className }: { usage: MachineUsage; t: Strings; lang: Lang; className: string }) {
  const rows = useMemo<Row[]>(() => {
    const out: Row[] = usage.machines.map((m, i) => ({
      id: m.id,
      label: m.label,
      note: m.this_machine ? t.machines.thisMachine : m.os,
      color: SHADES[i % SHADES.length],
      tokens: m.tokens,
      cost: m.cost_usd,
      values: m.values,
    }));
    if (usage.other.tokens > 0) {
      out.push({ id: "_other", label: t.machines.other, note: null, color: OTHER, tokens: usage.other.tokens, cost: null, values: usage.other.values });
    }
    return out;
  }, [usage, t]);
  const total = rows.reduce((s, r) => s + r.tokens, 0) || 1;
  const max = Math.max(...rows.map((r) => r.tokens), 1);

  return (
    <Panel
      id="s06"
      index="06"
      code="MC"
      title="Machines"
      sub={t.panels.machines}
      className={`machines ${className}`}
      meta={<span className="mono">{t.machines.count(usage.machines.length)}</span>}
    >
      <div className="mc-body">
        <div className="mc-list">
          <table className="tbl mc-tbl">
            <thead>
              <tr>
                <th className="lab">#</th>
                <th className="lab">{t.machines.machine}</th>
                <th className="lab barc" colSpan={2}>
                  {t.machines.share}
                </th>
                <th className="lab r">{t.machines.tokens}</th>
                <th className="lab r">{t.machines.cost}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={r.id} title={r.id === "_other" ? t.machines.otherNote : undefined}>
                  <td className="idx pix">
                    <i className="sw" style={{ background: r.color }} />
                    {r.id === "_other" ? "··" : `D${String(i + 1).padStart(2, "0")}`}
                  </td>
                  <td className="nm">
                    {r.label}
                    {r.note && <small>{r.note}</small>}
                  </td>
                  <td className="barc">
                    <Segs fraction={r.tokens / max} n={24} color={r.color} delay={0.3 + i * 0.05} />
                  </td>
                  <td className="r pct dim">{percent(r.tokens / total)}</td>
                  <td className="r">{tokens(r.tokens, lang)}</td>
                  <td className="r dim">{r.cost == null ? "—" : usd(r.cost, 0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {usage.other.tokens > 0 && <p className="mc-note">{`${t.machines.other}: ${t.machines.otherNote}`}</p>}
        </div>
        <div className="mc-chart">
          <Chart days={usage.days} rows={rows} t={t} lang={lang} />
        </div>
      </div>
    </Panel>
  );
}

function Chart({ days, rows, t, lang }: { days: string[]; rows: Row[]; t: Strings; lang: Lang }) {
  const [ref, { width: W, height: H }] = useSize<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const tip = useTooltip();
  const n = days.length;
  const totals = useMemo(() => days.map((_, i) => rows.reduce((s, r) => s + r.values[i], 0)), [days, rows]);
  const iw = W - PAD.left - PAD.right;
  const ih = H - PAD.top - PAD.bottom;
  const peak = Math.max(...totals, 0);
  const step = niceStep(peak);
  const top = Math.max(step, Math.ceil(peak / step) * step);
  const pitch = n ? iw / n : 0;
  const bw = Math.max(1.5, Math.min(18, pitch - (pitch > 5 ? 2 : 1)));
  const y = (v: number) => PAD.top + ih - (v / top) * ih;
  const ticks = Array.from({ length: Math.round(top / step) + 1 }, (_, k) => k * step);

  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const i = Math.floor((e.clientX - r.left - PAD.left) / pitch);
    if (i < 0 || i >= n) {
      setHover(null);
      tip.hide();
      return;
    }
    setHover(i);
    const lit = rows.filter((row) => row.values[i] > 0);
    tip.show(
      e,
      <>
        <div className="tip-head pix">{days[i]}</div>
        {lit.length ? (
          lit.map((row) => <TipRow key={row.id} color={row.color} label={row.label} value={tokens(row.values[i], lang, 2)} />)
        ) : (
          <TipRow label={t.tooltip.noUsage} value="" />
        )}
        {lit.length > 1 && <TipRow total label={t.tooltip.total} value={tokens(totals[i], lang, 2)} />}
      </>,
    );
  };

  return (
    <div className="fill" ref={ref} style={{ ["--min" as string]: "190px" }}>
      {W > 0 && H > 0 && n > 0 && (
        <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={t.machines.daily} onPointerMove={onMove} onPointerLeave={() => (setHover(null), tip.hide())}>
          {ticks.map((v) => (
            <g key={v}>
              <line className="gl" x1={PAD.left} x2={W - PAD.right} y1={y(v)} y2={y(v)} />
              <text className="ax" x={PAD.left - 10} y={y(v) + 3} textAnchor="end">
                {v ? tokens(v, lang) : "0"}
              </text>
            </g>
          ))}
          {days.map((d, i) => {
            const first = d.endsWith("-01");
            const x = PAD.left + (i + 0.5) * pitch;
            return (
              (first || (n <= 60 && i % 7 === 0)) && (
                <text key={d} className="ax" x={x + 3} y={H - 4}>
                  {first ? monthCode(d) : dotDate(d)}
                </text>
              )
            );
          })}
          {days.map((d, i) => {
            let base = 0;
            const x0 = PAD.left + i * pitch + (pitch - bw) / 2;
            return (
              <g key={d} className={hover != null && hover !== i ? "faded" : undefined}>
                {rows.map((row) => {
                  const v = row.values[i];
                  if (!v) return null;
                  const h = (v / top) * ih;
                  const gap = base > 0 && h > 3 ? 1 : 0;
                  const yTop = y(base + v);
                  base += v;
                  return (
                    <rect
                      key={row.id}
                      className="grow-y"
                      x={x0}
                      y={yTop}
                      width={bw}
                      height={Math.max(0.5, h - gap)}
                      fill={row.color}
                      style={{ animationDelay: `${(i / n) * 0.5}s` }}
                    />
                  );
                })}
              </g>
            );
          })}
        </svg>
      )}
    </div>
  );
}
