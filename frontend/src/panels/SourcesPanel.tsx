import type { SourceHealth, Status, Summary } from "../api/types";
import { Panel } from "../components/Panel";
import type { Lang, Strings } from "../i18n/strings";
import { percent, relativeTime } from "../lib/format";

export function sourceName(t: Strings, s: SourceHealth): string {
  const key = `${s.source}/${s.provider ?? ""}`;
  return t.sources.names[key]?.[0] ?? key;
}

// 07 · health of every source, then the ledger's own counts.
export function SourcesPanel({ status, summary, t, lang, className }: { status: Status; summary: Summary; t: Strings; lang: Lang; className: string }) {
  const coverage = summary.providers.codex?.cost_coverage;
  return (
    <Panel
      id="s07"
      index="07"
      code="SR"
      title="Sources"
      sub={t.panels.sources}
      className={`sources ${className}`}
      meta={coverage != null ? <span className="mono dim">{t.sources.codexCoverage(percent(coverage))}</span> : undefined}
    >
      <ul className="src">
        {status.sources.map((s, i) => {
          const key = `${s.source}/${s.provider ?? ""}`;
          const [name, detail] = t.sources.names[key] ?? [key, ""];
          return (
            <li key={key} className={s.ok ? "" : "bad"}>
              <span className="code pix">SRC-{String(i + 1).padStart(2, "0")}</span>
              <span className="nm">
                <b>{name}</b>
                <span className="lead" />
                <small>{detail}</small>
              </span>
              <span className={`state pix ${s.ok ? "" : "bad"}`}>{s.ok ? t.sources.ok : t.sources.fail}</span>
              <span className="when">{relativeTime(s.last_at, lang)}</span>
              {!s.ok && (
                <span className="err">
                  {s.error?.split("\n")[0]} · {s.last_ok_at ? t.sources.lastOk(relativeTime(s.last_ok_at, lang)) : t.sources.never}
                </span>
              )}
            </li>
          );
        })}
      </ul>
      <div className="stats">
        <Stat code="Rows" sub={t.sources.rows} value={status.ledger_rows} />
        <Stat code="Final" sub={t.sources.final} value={status.final_rows} />
        <Stat code="Open" sub={t.sources.open} value={status.open_rows} />
        <Stat code="Revs" sub={t.sources.revisions} value={status.revisions} />
      </div>
    </Panel>
  );
}

function Stat({ code, sub, value }: { code: string; sub: string; value: number }) {
  return (
    <div>
      <span className="lab">{code}</span>
      {sub && <span className="sub">{sub}</span>}
      <div className="v num">{value}</div>
    </div>
  );
}
