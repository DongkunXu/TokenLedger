import type { Daily, Status, Summary } from "../api/types";
import { Panel } from "../components/Panel";
import { type Lang, type Strings, providerName } from "../i18n/strings";
import { dotDate, tokenParts, tokens, usd } from "../lib/format";
import { useScramble } from "../lib/motion";
import { PROVIDER_ORDER, metricValue, sum } from "../lib/series";

// 00 · the headline number, the provider split, four readouts and today's strip.
export function TotalPanel({ summary, daily, status, t, lang }: { summary: Summary; daily: Daily; status: Status; t: Strings; lang: Lang }) {
  const [num, unit] = tokenParts(summary.tokens, lang, 2);
  const shown = useScramble(num, 800);
  const providers = PROVIDER_ORDER.filter((p) => (summary.providers[p]?.tokens ?? 0) > 0);
  const share = (p: string) => summary.providers[p].tokens / summary.tokens;
  const perDay = summary.active_days ? summary.tokens / summary.active_days : 0;

  return (
    <Panel
      id="s00"
      index="00"
      code="TK"
      title="Total tokens"
      sub={t.panels.total}
      className="c12 total"
      meta={
        <>
          <span className="lab">{t.total.span}</span>
          <span className="mono">
            {dotDate(summary.start)} → {dotDate(summary.end)} · {daily.days.length} D
          </span>
          <span className="lab">{t.total.basis}</span>
          <span className="mono">{t.total.basisValue}</span>
        </>
      }
    >
      <div className="total-body">
        <div className="total-main">
          <div className="scan" aria-hidden="true" />
          <div className="bignum">
            <span className="n num">{shown}</span>
            <span className="u">{unit}</span>
          </div>
          <div className="micro">
            <Micro label={t.total.exact} value={Math.round(summary.tokens).toLocaleString("en-US")} />
            <Micro label={t.total.unit} value={t.total.unitValue} />
            <Micro label={t.total.days} value={String(daily.days.length)} />
            <Micro label={t.total.rows} value={String(status.ledger_rows)} />
          </div>
          <ShareStrip providers={providers} share={share} />
          <div className="key">
            {providers.map((p) => (
              <span key={p} style={{ ["--c" as string]: `var(--${p}, var(--other))` }}>
                <i />
                {providerName(t, p)} <b>{tokens(summary.providers[p].tokens, lang)}</b> {(share(p) * 100).toFixed(0)}%
              </span>
            ))}
          </div>
        </div>
        <div className="readouts">
          <Readout code="Cost" sub={t.total.cost} note={t.total.costNote} value={usd(summary.cost_usd, 0)} />
          <Readout code="Active" sub={t.total.active} note={t.total.streak(summary.current_streak)} value={String(summary.active_days)} />
          <Readout
            code="Peak"
            sub={t.total.peak}
            note={summary.peak_day.day ? dotDate(summary.peak_day.day) : "—"}
            value={tokens(summary.peak_day.tokens, lang, 2)}
          />
          <Readout code="Avg" sub={t.total.avg} note={t.total.avgNote} value={tokens(perDay, lang, 2)} />
        </div>
      </div>
      <TodayStrip daily={daily} today={status.today} t={t} lang={lang} />
    </Panel>
  );
}

function Micro({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <span className="lab">{label}</span>
      <span className="mono">{value}</span>
    </div>
  );
}

function Readout({ code, sub, note, value }: { code: string; sub: string; note: string; value: string }) {
  return (
    <div className="readout">
      <span className="lab">{code}</span>
      {sub && <span className="sub">{sub}</span>}
      <span className="lead" />
      <span className="note">{note}</span>
      <span className="v num">{value}</span>
    </div>
  );
}

// 100 squares, coloured by each provider's share of tokens.
function ShareStrip({ providers, share }: { providers: string[]; share: (p: string) => number }) {
  const counts = providers.map((p) => Math.round(share(p) * 100));
  if (counts.length) counts[0] += 100 - sum(counts);
  let k = 0;
  return (
    <div className="strip" aria-hidden="true">
      {providers.flatMap((p, j) =>
        Array.from({ length: counts[j] }, () => {
          const i = k++;
          return <i key={i} style={{ background: `var(--${p}, var(--other))`, animationDelay: `${0.3 + i * 0.008}s` }} />;
        }),
      )}
    </div>
  );
}

function TodayStrip({ daily, today, t, lang }: { daily: Daily; today: string; t: Strings; lang: Lang }) {
  const i = daily.days.indexOf(today);
  if (i < 0) return null;
  const dayTokens = (j: number) => sum(daily.providers.map((p) => metricValue(p.values[j], "tokens")));
  const tok = dayTokens(i);
  const cost = sum(daily.providers.map((p) => metricValue(p.values[i], "cost")));
  const open = daily.providers.some((p) => p.values[i]?.open);
  const prev = Array.from({ length: Math.min(7, i) }, (_, k) => dayTokens(i - 1 - k));
  const avg = prev.length ? sum(prev) / prev.length : 0;
  return (
    <div className="today">
      <span className="tag pix">Today</span>
      <Item label={t.today.date} value={today} className="it-date" />
      <Item label={t.today.tokens} value={tokens(tok, lang, 2)} />
      <Item label={t.today.cost} value={usd(cost)} />
      {avg > 0 && <Item label={t.today.vsAvg} value={`${tok >= avg ? "+" : ""}${(((tok - avg) / avg) * 100).toFixed(0)}%`} className="it-extra" />}
      <Item label={t.today.state} value={open ? t.today.open : t.today.final} className="it-extra" />
      <span className="sp" />
      <span className="chev pix" aria-hidden="true">
        ◀◀◀
      </span>
    </div>
  );
}

function Item({ label, value, className = "" }: { label: string; value: string; className?: string }) {
  return (
    <span className={`it ${className}`}>
      <span className="lab">{label}</span>
      <b>{value}</b>
    </span>
  );
}
