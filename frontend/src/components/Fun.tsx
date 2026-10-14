import { useEffect, useRef, useState } from "react";
import type { Lang, Strings } from "../i18n/strings";
import { tokens } from "../lib/format";
import { FUN_GROUPS, FUN_ITEMS, type FunItem, type FunStats, duration, funValue, minutesSince } from "../lib/fun";
import { useScramble } from "../lib/motion";
import { GlassLayers } from "./Panel";
import { Toggle } from "./Toggle";

export type FunScope = "today" | "avg" | "range";
const SCOPES: FunScope[] = ["today", "avg", "range"];
const ROTATE_MS = 8000;

export interface FunInputs {
  today: string;
  todayTokens: number;
  start: string; // first day of the range
  rangeTokens: number;
  activeDays: number;
}

// Periods run until now, so the speed items move with the clock.
function scopesNow(p: FunInputs): Record<FunScope, FunStats> {
  return {
    today: { tokens: p.todayTokens, minutes: minutesSince(p.today) },
    avg: { tokens: p.activeDays ? p.rangeTokens / p.activeDays : 0, minutes: 24 * 60 },
    range: { tokens: p.rangeTokens, minutes: minutesSince(p.start) },
  };
}

// One random equivalent of the range's tokens, changing every few seconds; a click opens
// the full list.
export function FunLine({ inputs, t, lang }: { inputs: FunInputs; t: Strings; lang: Lang }) {
  const [i, setI] = useState(() => Math.floor(Math.random() * FUN_ITEMS.length));
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (open) return;
    const id = setInterval(() => setI((k) => (k + 1 + Math.floor(Math.random() * (FUN_ITEMS.length - 1))) % FUN_ITEMS.length), ROTATE_MS);
    return () => clearInterval(id);
  }, [open]);
  const item = FUN_ITEMS[i];
  const scopes = scopesNow(inputs);
  const value = useScramble(funValue(item, scopes.range, t, lang), 600);

  return (
    <>
      <button className="fun-line" onClick={() => setOpen(true)}>
        <span className="tag pix">{t.fun.code}</span>
        <span key={item.id} className="fun-text">
          <Sentence template={t.fun.items[item.id]} value={value} />
        </span>
        <span className="sp" />
        <span className="hint">{t.fun.hint} ›</span>
      </button>
      {open && <FunDialog scopes={scopes} t={t} lang={lang} onClose={() => setOpen(false)} />}
    </>
  );
}

function FunDialog({ scopes, t, lang, onClose }: { scopes: Record<FunScope, FunStats>; t: Strings; lang: Lang; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const [scope, setScope] = useState<FunScope>("range");
  const stats = scopes[scope];
  useEffect(() => {
    ref.current?.showModal();
  }, []);

  return (
    <dialog
      ref={ref}
      className="fun-dialog glass"
      onClose={onClose}
      onClick={(e) => e.target === ref.current && ref.current.close()}
      aria-label={t.fun.title || "Equivalents"}
    >
      <header className="ph">
        <div className="ph-id pix">
          <b>≈</b>
          <i>FN</i>
        </div>
        <h2 className="ph-title">
          <span className="en">Equivalents</span>
          {t.fun.title && <span className="sub">{t.fun.title}</span>}
        </h2>
        <span className="rule" aria-hidden="true" />
        <button className="sq" onClick={() => ref.current?.close()} aria-label={t.fun.close}>
          ✕
        </button>
      </header>
      <div className="fun-head">
        <Toggle label="scope" value={scope} onChange={setScope} options={SCOPES.map((s) => ({ value: s, label: t.fun.scopes[s] }))} />
        <div className="fun-stat">
          <span className="lab">{t.fun.tokens}</span>
          <span className="num">{tokens(stats.tokens, lang, 2)}</span>
        </div>
        <div className="fun-stat">
          <span className="lab">{t.fun.period}</span>
          <span className="num">{duration(stats.minutes, t, lang)}</span>
        </div>
      </div>
      <div className="fun-groups">
        {FUN_GROUPS.map((g) => (
          <section key={g} className="fun-group">
            <h3 className="lab">{t.fun.groups[g]}</h3>
            <ul>
              {FUN_ITEMS.filter((it) => it.group === g).map((it) => (
                <Row key={it.id} item={it} stats={stats} t={t} lang={lang} />
              ))}
            </ul>
          </section>
        ))}
      </div>
      <p className="fun-note">{t.fun.note}</p>
      <GlassLayers />
    </dialog>
  );
}

function Row({ item, stats, t, lang }: { item: FunItem; stats: FunStats; t: Strings; lang: Lang }) {
  const value = useScramble(funValue(item, stats, t, lang), 500);
  return (
    <li>
      <Sentence template={t.fun.items[item.id]} value={value} />
    </li>
  );
}

function Sentence({ template, value }: { template: string; value: string }) {
  const [before, after = ""] = template.split("{n}");
  return (
    <>
      {before}
      <b className="num">{value}</b>
      {after}
    </>
  );
}
