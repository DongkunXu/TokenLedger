import type { Status, Summary } from "../api/types";
import type { Lang, Strings } from "../i18n/strings";
import { percent, relativeTime } from "../lib/format";
import { Card } from "./ui";

const LABELS: Record<string, string> = {
  "codexbar/claude": "CodexBar · Claude",
  "codexbar/codex": "CodexBar · Codex",
  "codexbar/antigravity": "CodexBar · Antigravity",
  "codex-server/codex": "OpenAI · Codex",
  "codexbar-quota/": "CodexBar · quota",
};

export function SourcesPanel({ status, summary, t, lang, delay }: {
  status: Status;
  summary: Summary;
  t: Strings;
  lang: Lang;
  delay: number;
}) {
  const live = status.last_ok.filter((s) => s.kind !== "backfill");
  const coverage = summary.providers.codex?.cost_coverage;
  return (
    <Card title={t.sources.title} className="sources" delay={delay}>
      <ul className="source-list">
        {live.map((s) => {
          const key = `${s.source}/${s.provider ?? ""}`;
          return (
            <li key={key}>
              <span className="ok-dot" />
              <span className="source-name">{LABELS[key] ?? key}</span>
              <span className="source-time">{relativeTime(s.at, lang)}</span>
            </li>
          );
        })}
      </ul>
      <div className="source-facts">
        <span>{t.sources.final(status.final_rows)}</span>
        <span>{t.sources.open(status.open_rows)}</span>
        <span>{t.sources.revisions(status.revisions)}</span>
        {coverage != null && <span>{t.sources.codexCoverage(percent(coverage))}</span>}
      </div>
      {status.recent_failures.length > 0 && (
        <div className="source-failures">
          <div className="source-failures-title">{t.sources.failures}</div>
          {status.recent_failures.slice(0, 3).map((f) => (
            <div key={f.started_at + f.source} className="source-failure">
              <b>{f.source}</b> · {relativeTime(f.started_at, lang)} · {f.error.split("\n")[0]}
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
