import { useState } from "react";
import type { ModelRow } from "../api/types";
import { Segs } from "../components/Meters";
import { Panel } from "../components/Panel";
import { Toggle } from "../components/Toggle";
import { type Lang, type Strings, providerCode } from "../i18n/strings";
import { tokens, usd } from "../lib/format";

// 06 · every model in the range with a share bar. The list scrolls inside the panel, whose
// height follows its row.
export function ModelsPanel({ models, t, lang, className }: { models: ModelRow[]; t: Strings; lang: Lang; className: string }) {
  const [by, setBy] = useState<"tokens" | "cost">("tokens");
  const key = by === "cost" ? "cost_usd" : "tokens";
  const rows = [...models].sort((a, b) => (b[key] ?? -1) - (a[key] ?? -1));
  const max = rows[0]?.[key] || 1;

  return (
    <Panel
      id="s06"
      index="06"
      code="MD"
      title="Models"
      sub={t.panels.models}
      className={`models ${className}`}
      meta={
        <>
          <span className="mono">{t.models.count(rows.length)}</span>
          <Toggle label="sort" value={by} onChange={setBy} options={[{ value: "tokens", label: "Tok" }, { value: "cost", label: "$" }]} />
        </>
      }
    >
      <div className="fill" style={{ ["--min" as string]: "300px" }}>
        <div className="abs scroll">
          <table className="tbl">
            <thead>
              <tr>
                <th className="lab">#</th>
                <th />
                <th className="lab">{t.models.model}</th>
                <th className="lab barc">{by === "tokens" ? t.models.shareTokens : t.models.shareCost}</th>
                <th className="lab r">{t.models.tokens}</th>
                <th className="lab r">{t.models.cost}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((m, i) => {
                const color = `var(--${m.provider}, var(--other))`;
                return (
                  <tr key={`${m.provider}/${m.model}`} style={{ ["--c" as string]: color }}>
                    <td className="idx pix">M{String(i + 1).padStart(2, "0")}</td>
                    <td className="pv">
                      <span className="pix">{providerCode(m.provider)}</span>
                    </td>
                    <td className="nm" title={m.model}>
                      {m.model}
                    </td>
                    <td className="barc">
                      <Segs fraction={(m[key] ?? 0) / max} n={30} color={color} delay={0.3 + Math.min(i, 10) * 0.03} />
                    </td>
                    <td className="r">{tokens(m.tokens, lang)}</td>
                    <td className="r dim">{m.cost_usd == null ? t.models.unpriced : usd(m.cost_usd, 0)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </Panel>
  );
}
