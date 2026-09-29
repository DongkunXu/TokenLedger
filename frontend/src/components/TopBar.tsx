import { useEffect, useState } from "react";
import type { Status } from "../api/types";
import type { Lang, Strings } from "../i18n/strings";
import { relativeTime } from "../lib/format";
import type { ThemeChoice } from "../theme/theme";
import { Toggle } from "./Toggle";

export type RangeKey = "d30" | "d90" | "d180" | "y1" | "all";

const THEME_ORDER: ThemeChoice[] = ["system", "light", "dark"];
const THEME_GLYPH: Record<ThemeChoice, string> = { system: "◐", light: "○", dark: "●" };

export function TopBar(p: {
  t: Strings;
  lang: Lang;
  onLang: (l: Lang) => void;
  range: RangeKey;
  onRange: (r: RangeKey) => void;
  theme: ThemeChoice;
  onTheme: (c: ThemeChoice) => void;
  status: Status | null;
  lastCapture: string | null; // oldest "last success" across sources
  failing: string[]; // names of sources whose latest attempt failed
  capturing: boolean;
  captureError: string | null; // the capture request itself failed
  onCapture: () => void;
}) {
  const { t } = p;
  const nextTheme = THEME_ORDER[(THEME_ORDER.indexOf(p.theme) + 1) % THEME_ORDER.length];
  const alert = p.captureError ? `${t.top.captureFailed}: ${p.captureError}` : p.failing.length ? `${t.top.failing(p.failing.length)}: ${p.failing.join(" · ")}` : null;
  return (
    <header className="top">
      <div className="brand">
        <b>{t.appTitle}</b>
        <span>{t.appSubtitle}</span>
      </div>
      <div className="cells">
        {p.status && (
          <div className="cell cell-machine">
            <span className="lab">{t.top.machine}</span>
            <span className="v">
              {p.status.machine.id.toUpperCase()} · {p.status.timezone}
            </span>
          </div>
        )}
        <div className="cell cell-clock">
          <span className="lab">{t.top.time}</span>
          <Clock />
        </div>
        <div className={`cell cell-last ${alert ? "bad" : ""}`} title={alert ?? undefined}>
          <span className="lab">
            {t.top.lastOk} {t.top.lastOkSub && <span className="sub">· {t.top.lastOkSub}</span>}
          </span>
          <span className="v">
            <span className={`live ${alert ? "bad" : ""}`} />
            {alert ?? (p.lastCapture ? relativeTime(p.lastCapture, p.lang) : "—")}
          </span>
        </div>
      </div>
      <span className="sp" />
      <Toggle
        className="range"
        label="range"
        value={p.range}
        onChange={p.onRange}
        options={(["d30", "d90", "d180", "y1", "all"] as RangeKey[]).map((k) => ({ value: k, label: t.range[k] }))}
      />
      <div className="sq-group">
        <button className="sq" onClick={() => p.onTheme(nextTheme)} title={t.theme[p.theme]} aria-label={t.theme[p.theme]}>
          {THEME_GLYPH[p.theme]}
        </button>
        <button className="sq pix" onClick={() => p.onLang(p.lang === "zh" ? "en" : "zh")} aria-label="language">
          {t.langSwitch}
        </button>
      </div>
      <button className={`cap ${p.capturing ? "busy" : ""}`} onClick={p.onCapture} disabled={p.capturing}>
        <span className="cap-glyph">{p.capturing ? "◌" : "▶"}</span>
        {p.capturing ? t.top.capturing : t.top.capture}
        {t.top.captureSub && !p.capturing && <span className="sub">{t.top.captureSub}</span>}
      </button>
    </header>
  );
}

function Clock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(id);
  }, []);
  return <span className="v">{now.toLocaleTimeString("en-GB", { hour12: false })}</span>;
}
