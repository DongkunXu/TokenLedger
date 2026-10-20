import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "./api/client";
import type { Session, View } from "./api/types";
import { AccountDialog } from "./cloud/AccountDialog";
import { Enroll, type EnrollKind } from "./cloud/Enroll";
import { LinkPage } from "./cloud/LinkPage";
import { SignIn } from "./cloud/SignIn";
import { GlassLayers } from "./components/Panel";
import { Rail } from "./components/Rail";
import { TooltipProvider } from "./components/Tooltip";
import { type RangeKey, TopBar } from "./components/TopBar";
import { type Lang, STRINGS, type Strings } from "./i18n/strings";
import { usePointerLight } from "./lib/motion";
import { ActivityPanel } from "./panels/ActivityPanel";
import { DailyTokensPanel } from "./panels/DailyTokensPanel";
import { MachinesPanel } from "./panels/MachinesPanel";
import { ModelsPanel } from "./panels/ModelsPanel";
import { ProviderCards } from "./panels/ProviderCards";
import { QuotaPanel } from "./panels/QuotaPanel";
import { SourcesPanel, sourceName, syncProblems } from "./panels/SourcesPanel";
import { TotalPanel } from "./panels/TotalPanel";
import { WeeklyCostPanel } from "./panels/WeeklyCostPanel";
import { type ThemeChoice, useTheme } from "./theme/theme";

const SECTIONS = [
  { id: "s00", index: "00", title: "Total tokens" },
  { id: "s01", index: "01", title: "Providers" },
  { id: "s02", index: "02", title: "Quota" },
  { id: "s03", index: "03", title: "Activity" },
  { id: "s04", index: "04", title: "Daily tokens" },
  { id: "s05", index: "05", title: "Weekly cost" },
  { id: "s06", index: "06", title: "Machines" },
  { id: "s07", index: "07", title: "Models" },
  { id: "s08", index: "08", title: "Sources" },
];

const ENROLL: Record<string, EnrollKind> = { "/setup": "setup", "/join": "join", "/recover": "recover" };

function readLang(): Lang {
  try {
    const v = localStorage.getItem("tokenledger.lang");
    if (v === "zh" || v === "en") return v;
  } catch {
    /* ignore */
  }
  return navigator.language.toLowerCase().startsWith("zh") ? "zh" : "en";
}

export interface Chrome {
  t: Strings;
  lang: Lang;
  setLang: (l: Lang) => void;
  theme: ThemeChoice;
  setTheme: (c: ThemeChoice) => void;
}

// The local dashboard, or the cloud one: which page shows depends on the address and, in
// the cloud, on whether someone is signed in.
export default function App() {
  const [theme, setTheme] = useTheme();
  const [lang, setLangState] = useState<Lang>(readLang);
  const [session, setSession] = useState<Session | null>(null);
  const [error, setError] = useState<string | null>(null);
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
  const chrome: Chrome = { t: STRINGS[lang], lang, setLang, theme, setTheme };

  const refresh = useCallback(() => {
    api
      .session()
      .then((s) => (setSession(s), setError(null)))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);
  useEffect(refresh, [refresh]);

  const path = window.location.pathname.replace(/\/+$/, "") || "/";
  let page;
  if (!session) {
    page = (
      <div className="wrap">
        <StateNote text={error ? `${chrome.t.error} · ${error}` : chrome.t.loading} />
      </div>
    );
  } else if (session.mode === "cloud" && ENROLL[path]) {
    page = <Enroll kind={ENROLL[path]} setupNeeded={session.setup_needed} google={session.google} chrome={chrome} />;
  } else if (session.mode === "cloud" && !session.user) {
    page = <SignIn chrome={chrome} google={session.google} onSignedIn={refresh} />;
  } else if (session.mode === "cloud" && path === "/link") {
    page = <LinkPage chrome={chrome} />;
  } else {
    page = <Dashboard session={session} chrome={chrome} onSignedOut={refresh} />;
  }
  return <TooltipProvider>{page}</TooltipProvider>;
}

function Dashboard({ session, chrome, onSignedOut }: { session: Session; chrome: Chrome; onSignedOut: () => void }) {
  const { t, lang } = chrome;
  const cloud = session.mode === "cloud";
  const [range, setRange] = useState<RangeKey>("all");
  const [data, setData] = useState<View | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [capturing, setCapturing] = useState(false);
  const [captureError, setCaptureError] = useState<string | null>(null);
  const [account, setAccount] = useState(false);

  const load = useCallback(async () => {
    try {
      setData(await api.view(range));
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
  const failing = useMemo(() => {
    if (!data) return [];
    const several = data.status.machines.length > 1;
    const sources = data.status.sources
      .filter((s) => !s.ok)
      .map((s) => (several && s.machine_label ? `${sourceName(t, s)} @ ${s.machine_label}` : sourceName(t, s)));
    return [...sources, ...syncProblems(t, data.status)];
  }, [data, t]);
  const hasQuota = (data?.quota.length ?? 0) > 0;
  const empty = error && cloud && error.startsWith("no data yet") ? t.cloud.noData : null;

  return (
    <>
      <Rail sections={SECTIONS} version={data?.status.version ?? ""} />
      <div className="wrap">
        <TopBar
          t={t}
          lang={lang}
          onLang={chrome.setLang}
          range={range}
          onRange={setRange}
          theme={chrome.theme}
          onTheme={chrome.setTheme}
          status={data?.status ?? null}
          ledger={cloud ? (session.user?.name ?? null) : null}
          lastCapture={lastCapture}
          failing={failing}
          capturing={capturing}
          captureError={captureError}
          onCapture={capture}
          onAccount={cloud ? () => setAccount(true) : null}
        />

        {!data && <StateNote text={empty ?? (error ? `${t.error} · ${error}` : t.loading)} />}
        {data && data.summary.tokens === 0 && <StateNote text={cloud ? t.cloud.noData : t.empty} />}

        {data && data.summary.tokens > 0 && (
          <>
            <main className="grid">
              <TotalPanel summary={data.summary} daily={data.daily} status={data.status} t={t} lang={lang} />
              <ProviderCards summary={data.summary} daily={data.daily} models={data.models} t={t} lang={lang} />
              {hasQuota && <QuotaPanel readings={data.quota} peaks={data.peaks} t={t} className="c5" />}
              <ActivityPanel daily={data.daily} t={t} lang={lang} className={hasQuota ? "c7" : "c12"} />
              <DailyTokensPanel daily={data.daily} t={t} lang={lang} className="c8" />
              <WeeklyCostPanel daily={data.daily} t={t} className="c4" />
              <MachinesPanel usage={data.machines} t={t} lang={lang} className="c12" />
              <ModelsPanel models={data.models} t={t} lang={lang} className="c7" />
              <SourcesPanel status={data.status} summary={data.summary} t={t} lang={lang} className="c5" />
            </main>
            <footer className="foot">
              <span>{t.footer}</span>
              <span>
                TOKENLEDGER {data.status.version} · {cloud ? session.user?.name : data.status.machine.label}
              </span>
            </footer>
          </>
        )}
      </div>
      {account && session.mode === "cloud" && session.user && (
        <AccountDialog user={session.user} google={session.google} invites={session.invites} chrome={chrome} onClose={() => setAccount(false)} onSignedOut={onSignedOut} />
      )}
    </>
  );
}

export function StateNote({ text }: { text: string }) {
  return (
    <div className="state panel glass">
      <span className="pix">{">"}</span> {text}
      <GlassLayers />
    </div>
  );
}
