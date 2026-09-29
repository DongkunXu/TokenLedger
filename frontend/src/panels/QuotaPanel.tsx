import type { QuotaPeaks, QuotaReading } from "../api/types";
import { PixelColumns, Segs } from "../components/Meters";
import { Panel } from "../components/Panel";
import { type Strings, providerCode, providerName } from "../i18n/strings";
import { hhmm } from "../lib/format";
import { byProviderOrder } from "../lib/series";

const MAIN = 2; // providers shown as full meters; the rest share one line below

// A reading whose window has already reset (no capture since) counts as unused.
function current(r: QuotaReading | undefined, now: number) {
  if (!r) return null;
  const reset = r.resets_at ? new Date(r.resets_at).getTime() : null;
  const expired = reset != null && reset <= now;
  return { used: expired ? 0 : r.used_percent, reset: expired ? null : reset, expired };
}

// 02 · weekly and 5-hour limits per provider, with the last 16 weekly peaks.
export function QuotaPanel({ readings, peaks, t, className }: { readings: QuotaReading[]; peaks: QuotaPeaks; t: Strings; className: string }) {
  const now = Date.now();
  const providers = byProviderOrder([...new Set(readings.map((r) => r.provider))], (p) => p);
  const read = (p: string, w: string) => current(readings.find((r) => r.provider === p && r.window === w), now);
  const main = providers.slice(0, MAIN);
  const rest = providers.slice(MAIN);

  return (
    <Panel id="s02" index="02" code="QT" title="Quota" sub={t.panels.quota} className={`quota ${className}`} meta={<span className="lab">{t.quota.peakMark}</span>}>
      <div className="qgrid">
        {main.map((p) => {
          const weekly = read(p, "weekly");
          const session = read(p, "session");
          const past = (peaks.weekly.find((x) => x.provider === p)?.points ?? []).filter(([at]) => new Date(at).getTime() <= now);
          const lastPeak = past.length ? past[past.length - 1][1] : null;
          const history = past.slice(-15).map(([, v]) => v).concat(weekly ? [weekly.used] : []);
          const color = `var(--${p}, var(--other))`;
          return (
            <div className="q" key={p} style={{ ["--c" as string]: color }}>
              <div className="qh">
                <i />
                <b>{providerName(t, p)}</b>
                <span className="sp" />
                <span className="pix">{providerCode(p)}-Q</span>
              </div>
              <Meter label="Weekly" sub={t.quota.weekly} reading={weekly} peak={lastPeak} t={t} color={color} long />
              <Meter label="5 hours" sub={t.quota.session} reading={session} peak={null} t={t} color={color} />
              {history.length > 0 && (
                <div className="hist">
                  <span className="lab">Weekly peaks · 16 W</span>
                  <PixelColumns values={history} rows={10} color={color} cell={9} gap={2} highlightLast label={`${providerName(t, p)} weekly peaks`} />
                </div>
              )}
            </div>
          );
        })}
      </div>
      {rest.length > 0 && (
        <div className="qfoot">
          {rest.map((p) => (
            <span key={p}>
              {providerName(t, p)} · {t.quota.weekly || "weekly"} <b>{Math.round(read(p, "weekly")?.used ?? 0)}%</b> · {t.quota.session || "5h"}{" "}
              <b>{Math.round(read(p, "session")?.used ?? 0)}%</b>
            </span>
          ))}
          <span className="pix">{rest.map(providerCode).join(" ")}-Q</span>
        </div>
      )}
    </Panel>
  );
}

function Meter({ label, sub, reading, peak, t, color, long = false }: {
  label: string;
  sub: string;
  reading: ReturnType<typeof current>;
  peak: number | null;
  t: Strings;
  color: string;
  long?: boolean;
}) {
  const reset = reading?.reset != null ? new Date(reading.reset) : null;
  const resetText = reset ? (long ? `${reset.getMonth() + 1}/${reset.getDate()} ${hhmm(reset)}` : hhmm(reset)) : reading?.expired ? t.quota.wasReset : "—";
  return (
    <div className={`meter ${long ? "" : "sm"}`}>
      <div className="meter-top">
        <span className="lab">
          {label} {sub && <span className="sub">· {sub}</span>}
        </span>
        <span className="pct num">
          {reading ? Math.round(reading.used) : "—"}
          <small>%</small>
        </span>
      </div>
      <Segs fraction={(reading?.used ?? 0) / 100} n={40} color={color} marker={peak != null ? peak / 100 : null} markerTitle={peak != null ? `${Math.round(peak)}%` : undefined} />
      <div className="meter-foot">
        <span>{peak != null ? `${t.quota.lastWeekPeak} ${Math.round(peak)}%` : ""}</span>
        <span>
          {t.quota.reset} {resetText}
        </span>
      </div>
    </div>
  );
}
