import { useState } from "react";
import type { Chrome } from "../App";
import { api } from "../api/client";
import { Solo, message } from "./Solo";

// Signed out on the cloud dashboard: one button, the passkey does the rest.
export function SignIn({ chrome, onSignedIn }: { chrome: Chrome; onSignedIn: () => void }) {
  const { t } = chrome;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const go = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.signIn();
      onSignedIn();
    } catch (e) {
      setError(message(e) || null);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Solo chrome={chrome} code="IN" title="Sign in" sub={t.cloud.signInSub}>
      <p className="solo-text">{t.cloud.signInHint}</p>
      <button className="cap solo-go" onClick={go} disabled={busy}>
        <span className="cap-glyph">{busy ? "◌" : "▶"}</span>
        {busy ? t.cloud.signingIn : t.cloud.signIn}
      </button>
      {error && <p className="solo-err">{error}</p>}
    </Solo>
  );
}
