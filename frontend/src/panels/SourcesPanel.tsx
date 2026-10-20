import type { MachineInfo, SourceHealth, Status, Summary } from "../api/types";
import { Panel } from "../components/Panel";
import type { Lang, Strings } from "../i18n/strings";
import { percent, relativeTime } from "../lib/format";

export function sourceName(t: Strings, s: SourceHealth): string {
  const key = `${s.source}/${s.provider ?? ""}`;
  return t.sources.names[key]?.[0] ?? key;
}

// Sync problems the top bar should flag like a failing source.
export function syncProblems(t: Strings, status: Status): string[] {
  const sync = status.sync;
  if (sync?.signed_in && sync.last_ok === false) return [sync.signed_out ? t.sync.signedOut : t.sync.failed];
  return [];
}

// 08 · health of every source (on every machine), cloud sync and the other machines' data,
// then the ledger's own counts.
export function SourcesPanel({ status, summary, t, lang, className }: { status: Status; summary: Summary; t: Strings; lang: Lang; className: string }) {
  const coverage = summary.providers.codex?.cost_coverage;
  const several = status.machines.length > 1;
  // The cloud dashboard lists every machine with the code it runs; a local one shows only itself.
  const machines = status.scope === "ledger" ? status.machines : [];
  const sync = status.sync ?? { signed_in: false };
  let n = 0;
  const code = (prefix: string) => `${prefix}-${String(++n).padStart(2, "0")}`;
  return (
    <Panel
      id="s08"
      index="08"
      code="SR"
      title="Sources"
      sub={t.panels.sources}
      className={`sources ${className}`}
      meta={coverage != null ? <span className="mono dim">{t.sources.codexCoverage(percent(coverage))}</span> : undefined}
    >
      <ul className="src">
        {status.sources.map((s) => {
          const [name, detail] = t.sources.names[`${s.source}/${s.provider ?? ""}`] ?? [`${s.source}/${s.provider ?? ""}`, ""];
          return (
            <li key={`${s.machine}/${s.source}/${s.provider ?? ""}`} className={s.ok ? "" : "bad"}>
              <span className="code pix">{code("SRC")}</span>
              <span className="nm">
                <b>{name}</b>
                <span className="lead" />
                <small>{several && s.machine_label ? `${s.machine_label} · ${detail.split(" · ").slice(-1)[0]}` : detail}</small>
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
        {sync.signed_in && (
          <SyncRow
            code={code("SYN")}
            name={t.sync.cloud}
            detail={`${sync.user ?? ""} · ${sync.url?.replace(/^https?:\/\//, "") ?? ""}`}
            ok={sync.last_ok !== false}
            state={sync.last_ok === false ? t.sources.fail : t.sources.ok}
            when={sync.last_attempt_at ?? null}
            error={
              sync.last_ok === false
                ? `${sync.last_error ?? ""} · ${sync.last_ok_at ? t.sync.lastOk(relativeTime(sync.last_ok_at, lang)) : t.sync.never}`
                : null
            }
            lang={lang}
          />
        )}
        {machines.map((m: MachineInfo) => {
          const at = m.snapshot_at ?? sync.last_ok_at ?? null; // the machine that published this view has none
          return (
            <SyncRow
              key={m.id}
              code={code("M")}
              name={m.label}
              detail={[m.os, m.app_version, at && t.sync.dataOf(relativeTime(at, lang))].filter(Boolean).join(" · ")}
              ok
              state={null}
              when={at}
              error={null}
              lang={lang}
            />
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

function SyncRow(p: { code: string; name: string; detail: string; ok: boolean; state: string | null; when: string | null; error: string | null; lang: Lang }) {
  return (
    <li className={p.ok ? "" : "bad"}>
      <span className="code pix">{p.code}</span>
      <span className="nm">
        <b>{p.name}</b>
        <span className="lead" />
        <small>{p.detail}</small>
      </span>
      {p.state ? <span className={`state pix ${p.ok ? "" : "bad"}`}>{p.state}</span> : <span />}
      <span className="when">{p.when ? relativeTime(p.when, p.lang) : ""}</span>
      {p.error && <span className="err">{p.error}</span>}
    </li>
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
