import type { Daily, ModelRow, Summary } from "../api/types";
import { GlassLayers } from "../components/Panel";
import { PixelColumns } from "../components/Meters";
import { type Lang, type Strings, providerCode, providerName } from "../i18n/strings";
import { percent, tokenParts, tokens, usd } from "../lib/format";
import { useScramble } from "../lib/motion";
import { PROVIDER_ORDER, metricValue, sum } from "../lib/series";

// 01 · one stacked record card per provider.
export function ProviderCards({ summary, daily, models, t, lang }: { summary: Summary; daily: Daily; models: ModelRow[]; t: Strings; lang: Lang }) {
  const providers = PROVIDER_ORDER.filter((p) => (summary.providers[p]?.tokens ?? 0) > 0);
  return (
    <section id="s01" className="c12 cards">
      {providers.map((p, i) => (
        <ProviderCard key={p} provider={p} index={i} summary={summary} daily={daily} models={models} t={t} lang={lang} />
      ))}
    </section>
  );
}

function ProviderCard({ provider, index, summary, daily, models, t, lang }: {
  provider: string;
  index: number;
  summary: Summary;
  daily: Daily;
  models: ModelRow[];
  t: Strings;
  lang: Lang;
}) {
  const s = summary.providers[provider];
  const [num, unit] = tokenParts(s.tokens, lang, 1);
  const shown = useScramble(num, 700);
  const color = `var(--${provider}, var(--other))`;
  const series = daily.providers.find((x) => x.provider === provider);
  const account = series?.values.some((v) => v?.basis === "account") ?? false;
  const last30 = series ? series.values.slice(-30).map((v) => metricValue(v, "tokens")) : [];
  const top = models.filter((m) => m.provider === provider).sort((a, b) => b.tokens - a.tokens)[0];
  const priced = s.tokens * (s.cost_coverage ?? 0);
  const partsTotal = sum(Object.values(s.parts).map((v) => v ?? 0));
  const cacheShare = partsTotal ? (s.parts.cache_read ?? 0) / partsTotal : null;

  return (
    <article className={`card glass reveal ${index === 0 ? "focus" : ""}`} style={{ ["--c" as string]: color, animationDelay: `${0.1 + index * 0.08}s` }}>
      <div className="tag2 pix">
        <b>P{index + 1}</b>
        <i>{providerCode(provider)}</i>
      </div>
      <div className="card-main">
        <div className="card-head">
          <span className="en">
            Provider<b>{providerName(t, provider)}</b>
          </span>
          <span className="rule" aria-hidden="true" />
          <span className="ph-meta">
            <span className="lab">Basis</span>
            <span className="mono">{account ? t.provider.basisAccount : t.provider.basisMachine}</span>
          </span>
        </div>
        <div className="card-body">
          <div className="cell-big">
            <span className="lab">
              Tokens {t.provider.tokens && <span className="sub">· {t.provider.tokens}</span>}
            </span>
            <div className="big num">
              <span>{shown}</span>
              <small>{unit}</small>
            </div>
          </div>
          <Stat code="Share" value={percent(s.tokens / summary.tokens, 1)} note={t.provider.share} />
          <Stat
            code="Cost"
            value={usd(s.cost_usd)}
            note={s.cost_coverage != null && s.cost_coverage < 0.995 ? t.provider.coverage(percent(s.cost_coverage)) : t.provider.listPrice}
          />
          <Stat code="$ / M tok" value={priced > 0 ? usd((s.cost_usd / priced) * 1e6, 2) : "—"} note={t.provider.perMillion} />
          <Stat code="Cache read" value={cacheShare == null ? "—" : percent(cacheShare, 1)} note={t.provider.cacheRead} />
          <Stat code="Top model" value={top?.model ?? "—"} note={`${top ? tokens(top.tokens, lang) + " · " : ""}${t.provider.activeDays(s.active_days)}`} small />
          <div className="cell-spark">
            <span className="lab">Last {last30.length} d</span>
            <PixelColumns values={last30} rows={7} color={color} label={`${providerName(t, provider)} · last ${last30.length} days`} />
          </div>
        </div>
      </div>
      <GlassLayers />
    </article>
  );
}

function Stat({ code, value, note, small = false }: { code: string; value: string; note: string; small?: boolean }) {
  return (
    <div className="stat">
      <span className="lab">{code}</span>
      <div className={`v ${small ? "small" : ""}`} title={value}>
        {value}
      </div>
      <div className="h">{note}</div>
    </div>
  );
}
