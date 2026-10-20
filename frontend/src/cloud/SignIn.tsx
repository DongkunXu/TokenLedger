import { useState } from "react";
import type { Chrome } from "../App";
import { api } from "../api/client";
import { Solo, message } from "./Solo";

// Signed out on the cloud dashboard: Google when the deployment has it, a passkey otherwise
// (or as the other way in).
export function SignIn({ chrome, google, onSignedIn }: { chrome: Chrome; google: boolean; onSignedIn: () => void }) {
  const { t } = chrome;
  const [busy, setBusy] = useState(false);
  const [passkey, setPasskey] = useState(!google);
  const [error, setError] = useState<string | null>(() => new URLSearchParams(window.location.search).get("signin_error"));
  const go = async (how: "google" | "passkey") => {
    setBusy(true);
    setError(null);
    try {
      if (how === "google") {
        // Leaves the page; comes back to this address (an approval waiting at /link, say).
        await api.google(undefined, undefined, window.location.pathname + window.location.search);
        return;
      }
      await api.signIn();
      window.history.replaceState(null, "", "/");
      onSignedIn();
    } catch (e) {
      setError(message(e) || null);
    }
    setBusy(false);
  };
  return (
    <Solo chrome={chrome} code="IN" title="Sign in" sub={t.cloud.signInSub}>
      <p className="solo-text">{passkey ? t.cloud.signInHint : t.cloud.googleHint}</p>
      <button className="cap solo-go" onClick={() => go(passkey ? "passkey" : "google")} disabled={busy}>
        <span className="cap-glyph">{busy ? "◌" : "▶"}</span>
        {busy && passkey ? t.cloud.signingIn : passkey ? t.cloud.signIn : t.cloud.google}
      </button>
      {google && (
        <button className="link-quiet" onClick={() => setPasskey(!passkey)}>
          {passkey ? t.cloud.google : t.cloud.usePasskey}
        </button>
      )}
      {error && <p className="solo-err">{error}</p>}
    </Solo>
  );
}
