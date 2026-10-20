import { useEffect, useState } from "react";
import type { Chrome } from "../App";
import { api } from "../api/client";
import type { LinkRequest } from "../api/types";
import { relativeTime } from "../lib/format";
import { Solo, message } from "./Solo";

// /link?code=XXXX-XXXX: a computer ran `tokenledger sync login`; the signed-in person
// approves or denies it here.
export function LinkPage({ chrome }: { chrome: Chrome }) {
  const { t, lang } = chrome;
  const code = (new URLSearchParams(window.location.search).get("code") ?? "").toUpperCase();
  const [req, setReq] = useState<LinkRequest | null>(null);
  const [state, setState] = useState<"loading" | "ask" | "approved" | "denied" | "gone">("loading");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!code) {
      setState("gone");
      return;
    }
    api
      .linkRequest(code)
      .then((r) => (setReq(r), setState("ask")))
      .catch(() => setState("gone"));
  }, [code]);

  const answer = async (approve: boolean) => {
    setBusy(true);
    setError(null);
    try {
      if (approve) await api.approveLink(code);
      else await api.denyLink(code);
      setState(approve ? "approved" : "denied");
    } catch (e) {
      setError(message(e) || null);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Solo chrome={chrome} code="LK" title="Approve" sub={t.link.title}>
      {state === "gone" && <p className="solo-err">{t.link.gone}</p>}
      {state === "approved" && <p className="solo-text">{t.link.approved(req?.machine.label ?? "")}</p>}
      {state === "denied" && <p className="solo-text">{t.link.denied}</p>}
      {state === "ask" && req && (
        <>
          <p className="solo-text">{t.link.ask}</p>
          <dl className="kv">
            <dt className="lab">{chrome.t.machines.machine}</dt>
            <dd>
              <b>{req.machine.label}</b> <span className="dim">{req.machine.os} · TokenLedger {req.app_version}</span>
            </dd>
            <dt className="lab">{t.link.code}</dt>
            <dd className="pix code-big">{code}</dd>
            <dt className="lab">{t.link.from}</dt>
            <dd>
              {req.from || "—"} <span className="dim">· {relativeTime(req.requested_at, lang)}</span>
            </dd>
          </dl>
          <p className="solo-note">{t.link.codeHint}</p>
          {req.replaces && <p className="solo-note">{t.link.replaces(req.replaces.label)}</p>}
          <p className="solo-warn">{t.link.warn}</p>
          <div className="solo-actions">
            <button className="cap solo-go" onClick={() => answer(true)} disabled={busy}>
              <span className="cap-glyph">▶</span>
              {t.link.approve}
            </button>
            <button className="btn-quiet" onClick={() => answer(false)} disabled={busy}>
              {t.link.deny}
            </button>
          </div>
        </>
      )}
      {error && <p className="solo-err">{error}</p>}
    </Solo>
  );
}
