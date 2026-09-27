import type { Lang, Strings } from "../i18n/strings";
import { relativeTime } from "../lib/format";
import type { ThemeChoice } from "../theme/theme";
import { Segmented } from "./ui";

export type RangeKey = "d30" | "d90" | "d180" | "y1" | "all";

interface Props {
  t: Strings;
  lang: Lang;
  onLang: (l: Lang) => void;
  range: RangeKey;
  onRange: (r: RangeKey) => void;
  theme: ThemeChoice;
  onTheme: (c: ThemeChoice) => void;
  lastCapture: string | null;
  capturing: boolean;
  captureError: string | null;
  onCapture: () => void;
}

const THEME_ORDER: ThemeChoice[] = ["system", "light", "dark"];

export function Header(p: Props) {
  const nextTheme = THEME_ORDER[(THEME_ORDER.indexOf(p.theme) + 1) % THEME_ORDER.length];
  return (
    <header className="topbar">
      <div className="brand">
        <Logo />
        <div>
          <h1>{p.t.appTitle}</h1>
          <p>{p.t.appSubtitle}</p>
        </div>
      </div>
      <div className="topbar-controls">
        <Segmented
          label="range"
          value={p.range}
          onChange={p.onRange}
          options={(["d30", "d90", "d180", "y1", "all"] as RangeKey[]).map((k) => ({ value: k, label: p.t.range[k] }))}
        />
        <button className="icon-btn" onClick={() => p.onTheme(nextTheme)} title={p.t.theme[p.theme]} aria-label={p.t.theme[p.theme]}>
          <ThemeIcon choice={p.theme} />
        </button>
        <button className="icon-btn text" onClick={() => p.onLang(p.lang === "zh" ? "en" : "zh")} aria-label="language">
          {p.lang === "zh" ? "EN" : "中"}
        </button>
        <button className={`capture-btn ${p.capturing ? "busy" : ""}`} onClick={p.onCapture} disabled={p.capturing}>
          <span className="dot" />
          {p.capturing ? p.t.refreshing : p.t.refresh}
        </button>
      </div>
      <div className="topbar-meta">
        {p.captureError ? (
          <span className="meta-error">{p.t.refreshFailed}: {p.captureError}</span>
        ) : (
          p.lastCapture && (
            <span>
              {p.t.lastCapture} · {relativeTime(p.lastCapture, p.lang)}
            </span>
          )
        )}
      </div>
    </header>
  );
}

function Logo() {
  return (
    <svg className="logo" viewBox="0 0 32 32" aria-hidden="true">
      <rect x="3" y="17" width="6" height="12" rx="2" fill="var(--claude)" />
      <rect x="13" y="9" width="6" height="20" rx="2" fill="var(--codex)" />
      <rect x="23" y="3" width="6" height="26" rx="2" fill="var(--antigravity)" />
    </svg>
  );
}

function ThemeIcon({ choice }: { choice: ThemeChoice }) {
  if (choice === "light")
    return (
      <svg viewBox="0 0 20 20" width="16" height="16" aria-hidden="true">
        <circle cx="10" cy="10" r="3.6" fill="currentColor" />
        {[0, 45, 90, 135, 180, 225, 270, 315].map((a) => (
          <line key={a} x1="10" y1="1.8" x2="10" y2="4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" transform={`rotate(${a} 10 10)`} />
        ))}
      </svg>
    );
  if (choice === "dark")
    return (
      <svg viewBox="0 0 20 20" width="16" height="16" aria-hidden="true">
        <path d="M15.5 12.6A6.5 6.5 0 0 1 7.4 4.5a6.5 6.5 0 1 0 8.1 8.1Z" fill="currentColor" />
      </svg>
    );
  return (
    <svg viewBox="0 0 20 20" width="16" height="16" aria-hidden="true">
      <circle cx="10" cy="10" r="6.5" fill="none" stroke="currentColor" strokeWidth="1.6" />
      <path d="M10 3.5a6.5 6.5 0 0 1 0 13Z" fill="currentColor" />
    </svg>
  );
}
