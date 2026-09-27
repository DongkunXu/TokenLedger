import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type Span } from "./api/client";
import type { Daily, ModelRow, QuotaPeaks, Status, Summary } from "./api/types";
import { CompositionChart } from "./charts/CompositionChart";
import { Heatmap } from "./charts/Heatmap";
import { ModelChart } from "./charts/ModelChart";
import { QuotaChart } from "./charts/QuotaChart";
import { TrendChart, type TrendMode } from "./charts/TrendChart";
import { Header, type RangeKey } from "./components/Header";
import { KpiRow } from "./components/KpiRow";
import { SourcesPanel } from "./components/SourcesPanel";
import { Card, Segmented } from "./components/ui";
import { type Lang, STRINGS, providerName } from "./i18n/strings";
import { usd } from "./lib/format";
import { type Metric, PROVIDER_ORDER } from "./lib/series";
import { useTheme } from "./theme/theme";

const RANGE_DAYS: Record<RangeKey, number | null> = { d30: 30, d90: 90, d180: 182, y1: 365, all: null };

interface Data {
  status: Status;
  summary: Summary;
  daily: Daily;
  models: ModelRow[];
  quota: QuotaPeaks;
}

function spanFor(range: RangeKey, today: string): Span {
  const days = RANGE_DAYS[range];
  if (days == null) return { end: today };
  const d = new Date(`${today}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() - (days - 1));
  return { start: d.toISOString().slice(0, 10), end: today };
}

function readLang(): Lang {
  try {
    const v = localStorage.getItem("tokenledger.lang");
    if (v === "zh" || v === "en") return v;
  } catch {
    /* ignore */
  }
  return "zh";
}

export default function App() {
  const [themeChoice, setTheme, resolved] = useTheme();
  const [lang, setLangState] = useState<Lang>(readLang);
  const t = STRINGS[lang];
  const [range, setRange] = useState<RangeKey>("all");
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [capturing, setCapturing] = useState(false);
  const [captureError, setCaptureError] = useState<string | null>(null);
  const [heatMetric, setHeatMetric] = useState<Metric>("tokens");
  const [heatProvider, setHeatProvider] = useState("all");
  const [trendMode, setTrendMode] = useState<TrendMode>("tokens");
  const [modelBy, setModelBy] = useState<"tokens" | "cost">("tokens");
  const [quotaWindow, setQuotaWindow] = useState<"weekly" | "session">("weekly");

  const setLang = (l: Lang) => {
    setLangState(l);
    try {
      localStorage.setItem("tokenledger.lang", l);
    } catch {
      /* ignore */
    }
  };

  const load = useCallback(async () => {
    try {
      const status = await api.status();
      const span = spanFor(range, status.today);
      const [summary, daily, models, quota] = await Promise.all([
        api.summary(span),
        api.daily(span),
        api.models(span),
        api.quotaPeaks(span),
      ]);
      setData({ status, summary, daily, models, quota });
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [range]);

  useEffect(() => {
    load();
  }, [load]);

  const capture = async () => {
    setCapturing(true);
    setCaptureError(null);
    try {
      const outcomes = await api.capture();
      const failed = outcomes.filter((o) => !o.ok);
      if (failed.length) setCaptureError(failed.map((o) => `${o.source}: ${o.error}`).join("; "));
      await load();
    } catch (e) {
      setCaptureError(e instanceof Error ? e.message : String(e));
    } finally {
      setCapturing(false);
    }
  };

  const themeKey = `${resolved}-${lang}`;
  const lastCapture = useMemo(() => {
    const times = data?.status.last_ok.filter((s) => s.kind === "usage").map((s) => s.at) ?? [];
    return times.length ? times.sort().at(-1)! : null;
  }, [data]);

  const heatProviders = useMemo(() => {
    const present = data?.daily.providers.map((p) => p.provider) ?? [];
    return PROVIDER_ORDER.filter((p) => present.includes(p));
  }, [data]);

  return (
    <div className="page">
      <Header
        t={t}
        lang={lang}
        onLang={setLang}
        range={range}
        onRange={setRange}
        theme={themeChoice}
        onTheme={setTheme}
        lastCapture={lastCapture}
        capturing={capturing}
        captureError={captureError}
        onCapture={capture}
      />

      {error && !data && <div className="state">{t.error} · {error}</div>}
      {!error && !data && <div className="state">{t.loading}</div>}

      {data && data.summary.tokens === 0 && <div className="state">{t.empty}</div>}

      {data && data.summary.tokens > 0 && (
        <main>
          <KpiRow summary={data.summary} t={t} lang={lang} />

          <Card
            title={t.heatmap.title}
            delay={240}
            actions={
              <>
                <Segmented
                  label="provider"
                  value={heatProvider}
                  onChange={setHeatProvider}
                  options={[{ value: "all", label: t.heatmap.all }, ...heatProviders.map((p) => ({ value: p, label: providerName(t, p) }))]}
                />
                <Segmented
                  label="metric"
                  value={heatMetric}
                  onChange={setHeatMetric}
                  options={[
                    { value: "tokens", label: t.heatmap.tokens },
                    { value: "cost", label: t.heatmap.cost },
                  ]}
                />
              </>
            }
          >
            <Heatmap daily={data.daily} metric={heatMetric} provider={heatProvider} t={t} lang={lang} themeKey={themeKey} />
          </Card>

          <Card
            title={t.trend.title}
            delay={300}
            actions={
              <Segmented
                label="trend"
                value={trendMode}
                onChange={setTrendMode}
                options={[
                  { value: "tokens", label: t.trend.tokens },
                  { value: "cost", label: t.trend.cost },
                  { value: "cumulative", label: t.trend.cumulative },
                ]}
              />
            }
          >
            <TrendChart daily={data.daily} mode={trendMode} t={t} lang={lang} themeKey={themeKey} />
          </Card>

          <div className="grid-2">
            <Card
              title={t.models.title}
              delay={360}
              actions={
                <Segmented
                  label="models"
                  value={modelBy}
                  onChange={setModelBy}
                  options={[
                    { value: "tokens", label: t.models.byTokens },
                    { value: "cost", label: t.models.byCost },
                  ]}
                />
              }
            >
              <ModelChart models={data.models} by={modelBy} t={t} lang={lang} themeKey={themeKey} />
            </Card>

            <div className="stack">
              <Card title={t.composition.title} delay={420}>
                <CompositionChart summary={data.summary} t={t} lang={lang} themeKey={themeKey} />
                <div className="per-million">
                  {PROVIDER_ORDER.filter((p) => (data.summary.providers[p]?.cost_usd ?? 0) > 0).map((p) => {
                    const s = data.summary.providers[p];
                    const covered = s.tokens * (s.cost_coverage ?? 0);
                    return (
                      <span key={p}>
                        <i style={{ background: `var(--${p})` }} />
                        {providerName(t, p)} {usd((s.cost_usd / covered) * 1e6, 2)} <em>{t.composition.perMillion}</em>
                      </span>
                    );
                  })}
                </div>
              </Card>
              <SourcesPanel status={data.status} summary={data.summary} t={t} lang={lang} delay={480} />
            </div>
          </div>

          {data.quota.weekly.length + data.quota.session.length > 0 && (
            <Card
              title={t.quota.title}
              delay={540}
              actions={
                <Segmented
                  label="quota"
                  value={quotaWindow}
                  onChange={setQuotaWindow}
                  options={[
                    { value: "weekly", label: t.quota.weekly },
                    { value: "session", label: t.quota.session },
                  ]}
                />
              }
            >
              <QuotaChart peaks={data.quota} window={quotaWindow} t={t} lang={lang} themeKey={themeKey} />
            </Card>
          )}

          <footer className="footer">
            {t.footer} · {data.status.machine.label} · {data.status.timezone} · v{data.status.version}
          </footer>
        </main>
      )}
    </div>
  );
}
