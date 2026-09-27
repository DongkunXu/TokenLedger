import type { Summary } from "../api/types";
import { type Lang, type Strings, providerName } from "../i18n/strings";
import { shortDate, tokens as fmtTokens, usd } from "../lib/format";
import { PROVIDER_ORDER } from "../lib/series";
import { CountUp } from "./ui";

export function KpiRow({ summary, t, lang }: { summary: Summary; t: Strings; lang: Lang }) {
  const providers = PROVIDER_ORDER.filter((p) => (summary.providers[p]?.tokens ?? 0) > 0);
  const split = (key: "tokens" | "cost_usd") =>
    providers.map((p) => ({ p, v: summary.providers[p][key] })).filter((x) => x.v > 0);

  return (
    <div className="kpis">
      <Kpi label={t.kpi.tokens} delay={0}>
        <div className="kpi-value">
          <CountUp value={summary.tokens} format={(n) => fmtTokens(n, lang, 2)} />
        </div>
        <SplitBar parts={split("tokens")} total={summary.tokens} format={(n) => fmtTokens(n, lang)} t={t} />
      </Kpi>
      <Kpi label={t.kpi.cost} hint={t.kpi.costHint} delay={60}>
        <div className="kpi-value">
          <CountUp value={summary.cost_usd} format={(n) => usd(n, 0)} />
        </div>
        <SplitBar parts={split("cost_usd")} total={summary.cost_usd} format={(n) => usd(n, 0)} t={t} />
      </Kpi>
      <Kpi label={t.kpi.activeDays} delay={120}>
        <div className="kpi-value">
          <CountUp value={summary.active_days} format={(n) => String(Math.round(n))} />
        </div>
        <div className="kpi-sub">{t.kpi.streak(summary.current_streak)}</div>
      </Kpi>
      <Kpi label={t.kpi.peak} delay={180}>
        <div className="kpi-value">
          <CountUp value={summary.peak_day.tokens} format={(n) => fmtTokens(n, lang, 2)} />
        </div>
        <div className="kpi-sub">{summary.peak_day.day ? shortDate(summary.peak_day.day, lang) : "—"}</div>
      </Kpi>
    </div>
  );
}

function Kpi({ label, hint, delay, children }: { label: string; hint?: string; delay: number; children: React.ReactNode }) {
  return (
    <section className="card kpi" style={{ animationDelay: `${delay}ms` }}>
      <div className="kpi-label">
        {label}
        {hint && <span className="kpi-hint">{hint}</span>}
      </div>
      {children}
    </section>
  );
}

function SplitBar({ parts, total, format, t }: {
  parts: { p: string; v: number }[];
  total: number;
  format: (n: number) => string;
  t: Strings;
}) {
  if (!total) return <div className="kpi-sub">—</div>;
  return (
    <div className="split">
      <div className="split-bar">
        {parts.map(({ p, v }) => (
          <span key={p} style={{ width: `${(v / total) * 100}%`, background: `var(--${p}, var(--other))` }} />
        ))}
      </div>
      <div className="split-legend">
        {parts.map(({ p, v }) => (
          <span key={p}>
            <i style={{ background: `var(--${p}, var(--other))` }} />
            {providerName(t, p)} {format(v)}
          </span>
        ))}
      </div>
    </div>
  );
}
