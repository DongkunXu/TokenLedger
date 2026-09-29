import type { Daily } from "../api/types";
import { type Lang, type Strings, providerName } from "../i18n/strings";
import { tokens, usd } from "../lib/format";
import { orderedProviders } from "../lib/series";
import { TipRow } from "./Tooltip";

// Tooltip body for one day: tokens per provider, then the day's total tokens and cost.
export function DayTip({ daily, index, t, lang }: { daily: Daily; index: number; t: Strings; lang: Lang }) {
  let totalTokens = 0;
  let totalCost = 0;
  const rows = orderedProviders(daily).flatMap((p) => {
    const v = p.values[index];
    if (!v || v.tokens <= 0) return [];
    totalTokens += v.tokens;
    totalCost += v.cost_usd ?? 0;
    const notes = [v.basis === "account" ? t.tooltip.account : null, v.open ? t.tooltip.open : null].filter(Boolean).join(" · ");
    return [
      <TipRow
        key={p.provider}
        color={`var(--${p.provider}, var(--other))`}
        label={
          <>
            {providerName(t, p.provider)}
            {notes && <em>{notes}</em>}
          </>
        }
        value={tokens(v.tokens, lang, 2)}
      />,
    ];
  });
  return (
    <>
      <div className="tip-head pix">{daily.days[index]}</div>
      {rows.length ? rows : <TipRow label={t.tooltip.noUsage} value="" />}
      {rows.length > 0 && <TipRow total label={t.tooltip.total} value={`${tokens(totalTokens, lang, 2)} · ${usd(totalCost)}`} />}
    </>
  );
}
