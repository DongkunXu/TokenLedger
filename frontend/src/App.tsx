import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type Span } from "./api/client";
import type { Daily, ModelRow, QuotaPeaks, QuotaReading, Status, Summary } from "./api/types";
import { GlassLayers } from "./components/Panel";
import { Rail } from "./components/Rail";
import { TooltipProvider } from "./components/Tooltip";
import { type RangeKey, TopBar } from "./components/TopBar";
import { type Lang, STRINGS } from "./i18n/strings";
import { usePointerLight } from "./lib/motion";
import { ActivityPanel } from "./panels/ActivityPanel";
import { DailyTokensPanel } from "./panels/DailyTokensPanel";
import { ModelsPanel } from "./panels/ModelsPanel";
import { ProviderCards } from "./panels/ProviderCards";
import { QuotaPanel } from "./panels/QuotaPanel";
import { SourcesPanel, sourceName } from "./panels/SourcesPanel";
import { TotalPanel } from "./panels/TotalPanel";
import { WeeklyCostPanel } from "./panels/WeeklyCostPanel";
import { useTheme } from "./theme/theme";

const RANGE_DAYS: Record<RangeKey, number | null> = { d30: 30, d90: 90, d180: 182, y1: 365, all: null };

const SECTIONS = [
  { id: "s00", index: "00", title: "Total tokens" },
  { id: "s01", index: "01", title: "Providers" },
  { id: "s02", index: "02", title: "Quota" },
  { id: "s03", index: "03", title: "Activity" },
  { id: "s04", index: "04", title: "Daily tokens" },
  { id: "s05", index: "05", title: "Weekly cost" },
  { id: "s06", index: "06", title: "Models" },
  { id: "s07", index: "07", title: "Sources" },
];

interface Data {
  status: Status;
  summary: Summary;
  daily: Daily;
  models: ModelRow[];
  peaks: QuotaPeaks;
  quota: QuotaReading[];
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
  const [themeChoice, setTheme] = useTheme();
  const [lang, setLangState] = useState<Lang>(readLang);
  const t = STRINGS[lang];
  const [range, setRange] = useState<RangeKey>("all");
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [capturing, setCapturing] = useState(false);
  const [captureError, setCaptureError] = useState<string | null>(null);
  usePointerLight();

  const setLang = (l: Lang) => {
    setLangState(l);
    document.documentElement.lang = l === "zh" ? "zh-CN" : "en";
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
      const [summary, daily, models, peaks, quota] = await Promise.all([
        api.summary(span),
        api.daily(span),
        api.models(span),
        api.quotaPeaks(span),
        api.quotaNow(),
      ]);
      setData({ status, summary, daily, models, peaks, quota });
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
      // Per-source failures are recorded by the backend and shown from the status on reload.
      await api.capture();
      await load();
    } catch (e) {
      setCaptureError(e instanceof Error ? e.message : String(e));
    } finally {
      setCapturing(false);
    }
  };

  // When all data was last fully up to date: the oldest "last success" across sources.
  // A failing source holds this back, so a failed attempt never looks like a fresh update.
  const lastCapture = useMemo(() => {
    const times = data?.status.sources.map((s) => s.last_ok_at).filter((x): x is string => x != null) ?? [];
    return times.length ? times.sort()[0] : null;
  }, [data]);
  const failing = useMemo(() => data?.status.sources.filter((s) => !s.ok).map((s) => sourceName(t, s)) ?? [], [data, t]);
  const hasQuota = (data?.quota.length ?? 0) > 0;

  return (
    <TooltipProvider>
      <Rail sections={SECTIONS} version={data?.status.version ?? ""} />
      <div className="wrap">
        <TopBar
          t={t}
          lang={lang}
          onLang={setLang}
          range={range}
          onRange={setRange}
          theme={themeChoice}
          onTheme={setTheme}
          status={data?.status ?? null}
          lastCapture={lastCapture}
          failing={failing}
          capturing={capturing}
          captureError={captureError}
          onCapture={capture}
        />

        {!data && <StateNote text={error ? `${t.error} · ${error}` : t.loading} />}
        {data && data.summary.tokens === 0 && <StateNote text={t.empty} />}

        {data && data.summary.tokens > 0 && (
          <>
            <main className="grid">
              <TotalPanel summary={data.summary} daily={data.daily} status={data.status} t={t} lang={lang} />
              <ProviderCards summary={data.summary} daily={data.daily} models={data.models} t={t} lang={lang} />
              {hasQuota && <QuotaPanel readings={data.quota} peaks={data.peaks} t={t} className="c5" />}
              <ActivityPanel daily={data.daily} t={t} lang={lang} className={hasQuota ? "c7" : "c12"} />
              <DailyTokensPanel daily={data.daily} t={t} lang={lang} className="c8" />
              <WeeklyCostPanel daily={data.daily} t={t} className="c4" />
              <ModelsPanel models={data.models} t={t} lang={lang} className="c7" />
              <SourcesPanel status={data.status} summary={data.summary} t={t} lang={lang} className="c5" />
            </main>
            <footer className="foot">
              <span>{t.footer}</span>
              <span>
                TOKENLEDGER {data.status.version} · {data.status.machine.label}
              </span>
            </footer>
          </>
        )}
      </div>
    </TooltipProvider>
  );
}

function StateNote({ text }: { text: string }) {
  return (
    <div className="state panel glass">
      <span className="pix">{">"}</span> {text}
      <GlassLayers />
    </div>
  );
}
