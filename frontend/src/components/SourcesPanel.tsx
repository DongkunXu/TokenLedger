import type { SourceHealth, Status, Summary } from "../api/types";
import type { Lang, Strings } from "../i18n/strings";
import { percent, relativeTime } from "../lib/format";
import { Card } from "./ui";

export function sourceName(t: Strings, s: SourceHealth): string {
  const key = `${s.source}/${s.provider ?? ""}`;
  return t.sources.names[key] ?? key;
}

export function SourcesPanel({ status, summary, t, lang, delay }: {
  status: Status;
  summary: Summary;
  t: Strings;
  lang: Lang;
  delay: number;
}) {
  const coverage = summary.providers.codex?.cost_coverage;
  return (
    <Card title={t.sources.title} className="sources" delay={delay}>
      <ul className="source-list">
        {status.sources.map((s) => (
          <li key={`${s.source}/${s.provider ?? ""}`} className={s.ok ? "" : "failing"}>
            <div className="source-row">
              <span className="status-dot" />
              <span className="source-name">{sourceName(t, s)}</span>
              <span className="source-time">
                {s.ok
                  ? relativeTime(s.last_at, lang)
                  : `${t.sources.failed} · ${relativeTime(s.last_at, lang)}`}
              </span>
            </div>
            {!s.ok && (
              <div className="source-error">
                {s.error?.split("\n")[0]}
                <span>
                  {" · "}
                  {s.last_ok_at ? t.sources.lastOk(relativeTime(s.last_ok_at, lang)) : t.sources.never}
                </span>
              </div>
            )}
          </li>
        ))}
      </ul>
      <div className="source-facts">
        <span>{t.sources.final(status.final_rows)}</span>
        <span>{t.sources.open(status.open_rows)}</span>
        <span>{t.sources.revisions(status.revisions)}</span>
        {coverage != null && <span>{t.sources.codexCoverage(percent(coverage))}</span>}
      </div>
    </Card>
  );
}
