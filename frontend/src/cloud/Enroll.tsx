import { useState } from "react";
import type { Chrome } from "../App";
import { api } from "../api/client";
import { Solo, message } from "./Solo";

export type EnrollKind = "setup" | "join" | "recover";

// /setup#code, /join#code, /recover#code: create the first account, accept an invitation, or
// add a way to sign in to an account that lost its own. Google when the deployment has it, a
// passkey otherwise (or as the other way). The code stays in the address's fragment, which
// browsers never send to the server.
export function Enroll({ kind, setupNeeded, google, chrome }: { kind: EnrollKind; setupNeeded: boolean; google: boolean; chrome: Chrome }) {
  const { t } = chrome;
  const code = decodeURIComponent(window.location.hash.slice(1));
  const [name, setName] = useState("");
  const [passkey, setPasskey] = useState(!google);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(code ? null : t.cloud.noCode);
  const needsName = kind !== "recover";
  const title = { setup: t.cloud.setupTitle, join: t.cloud.joinTitle, recover: t.cloud.recoverTitle }[kind];
  const ready = !busy && !done && !!code && (!needsName || !!name.trim());

  const go = async () => {
    setBusy(true);
    setError(null);
    try {
      if (!passkey) {
        await api.google(code, needsName ? name.trim() : undefined); // leaves the page
        return;
      }
      await api.addPasskey(code, needsName ? name.trim() : undefined);
      setDone(true);
      window.location.replace("/"); // signed in; the code is spent
    } catch (e) {
      setError(message(e) || null);
    }
    setBusy(false);
  };

  return (
    <Solo chrome={chrome} code={kind === "recover" ? "RK" : "ID"} title={kind === "setup" ? "Set up" : kind === "join" ? "Join" : "Recover"} sub={title}>
      {kind === "recover" && <p className="solo-text">{t.cloud.recoverHint}</p>}
      {kind === "setup" && !setupNeeded && <p className="solo-err">{t.link.gone}</p>}
      {needsName && (
        <label className="field">
          <span className="lab">{t.cloud.name}</span>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={t.cloud.namePlaceholder}
            maxLength={40}
            autoComplete={passkey ? "username webauthn" : "nickname"}
            autoFocus
            onKeyDown={(e) => e.key === "Enter" && ready && go()}
          />
        </label>
      )}
      <button className="cap solo-go" onClick={go} disabled={!ready}>
        <span className="cap-glyph">{busy ? "◌" : "▶"}</span>
        {done ? t.cloud.done : passkey ? t.cloud.create : t.cloud.createGoogle}
      </button>
      <p className="solo-note">{passkey ? t.cloud.signInHint : t.cloud.googleEnrollHint}</p>
      {google && (
        <button className="link-quiet" onClick={() => setPasskey(!passkey)}>
          {passkey ? t.cloud.createGoogle : t.cloud.usePasskey}
        </button>
      )}
      {error && <p className="solo-err">{error}</p>}
    </Solo>
  );
}
