import type { ReactNode } from "react";
import type { Chrome } from "../App";
import { GlassLayers } from "../components/Panel";

const THEME_ORDER = ["system", "light", "dark"] as const;
const THEME_GLYPH = { system: "◐", light: "○", dark: "●" } as const;

// A page with one card in the middle (signing in, accounts, approving a computer): the
// brand and the theme and language switches on top, no rail.
export function Solo({ chrome, code, title, sub, children }: { chrome: Chrome; code: string; title: string; sub?: string; children: ReactNode }) {
  const { t } = chrome;
  const next = THEME_ORDER[(THEME_ORDER.indexOf(chrome.theme) + 1) % THEME_ORDER.length];
  return (
    <div className="solo">
      <header className="top solo-top">
        <div className="brand">
          <b>{t.appTitle}</b>
          <span>{t.appSubtitle}</span>
        </div>
        <span className="sp" />
        <div className="sq-group">
          <button className="sq" onClick={() => chrome.setTheme(next)} title={t.theme[chrome.theme]} aria-label={t.theme[chrome.theme]}>
            {THEME_GLYPH[chrome.theme]}
          </button>
          <button className="sq pix" onClick={() => chrome.setLang(chrome.lang === "zh" ? "en" : "zh")} aria-label="language">
            {t.langSwitch}
          </button>
        </div>
      </header>
      <section className="panel glass reveal solo-card">
        <header className="ph">
          <div className="ph-id pix">
            <b>◎</b>
            <i>{code}</i>
          </div>
          <h2 className="ph-title">
            <span className="en">{title}</span>
            {sub && <span className="sub">{sub}</span>}
          </h2>
          <span className="rule" aria-hidden="true" />
        </header>
        <div className="solo-body">{children}</div>
        <GlassLayers />
      </section>
    </div>
  );
}

export function message(e: unknown): string {
  if (e instanceof Error) {
    // The browser's own wording when the person closes the passkey prompt.
    if (e.name === "NotAllowedError" || e.name === "AbortError") return "";
    return e.message;
  }
  return String(e);
}
