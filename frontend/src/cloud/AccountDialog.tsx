import { useCallback, useEffect, useRef, useState } from "react";
import type { Chrome } from "../App";
import { api } from "../api/client";
import type { Device, PasskeyInfo } from "../api/types";
import { GlassLayers } from "../components/Panel";
import { relativeTime } from "../lib/format";
import { message } from "./Solo";

// The cloud dashboard's account menu: computers signed in to this ledger, passkeys,
// invitations (admins), signing out.
export function AccountDialog({ user, chrome, onClose, onSignedOut }: {
  user: { name: string; admin: boolean };
  chrome: Chrome;
  onClose: () => void;
  onSignedOut: () => void;
}) {
  const { t, lang } = chrome;
  const ref = useRef<HTMLDialogElement>(null);
  const [devices, setDevices] = useState<Device[] | null>(null);
  const [passkeys, setPasskeys] = useState<PasskeyInfo[] | null>(null);
  const [invite, setInvite] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(() => {
    api.devices().then((d) => setDevices(d.devices)).catch((e) => setError(message(e)));
    api.passkeys().then((p) => setPasskeys(p.passkeys)).catch((e) => setError(message(e)));
  }, []);
  useEffect(() => {
    ref.current?.showModal();
    reload();
  }, [reload]);

  const run = async (action: () => Promise<unknown>) => {
    setError(null);
    try {
      await action();
      reload();
    } catch (e) {
      setError(message(e) || null);
    }
  };

  return (
    <dialog ref={ref} className="fun-dialog acct glass" onClose={onClose} onClick={(e) => e.target === ref.current && ref.current.close()} aria-label={t.cloud.account}>
      <header className="ph">
        <div className="ph-id pix">
          <b>◎</b>
          <i>AC</i>
        </div>
        <h2 className="ph-title">
          <span className="en">Account</span>
          <span className="sub">{user.name}</span>
        </h2>
        <span className="rule" aria-hidden="true" />
        <button className="sq" onClick={() => ref.current?.close()} aria-label={t.cloud.close}>
          ✕
        </button>
      </header>

      <section className="acct-sec">
        <h3 className="lab">{t.cloud.devices}</h3>
        {devices && devices.length === 0 && <p className="solo-note">{t.cloud.devicesNone}</p>}
        <ul className="acct-list">
          {devices?.map((d) => (
            <li key={d.id}>
              <span className="nm">
                <b>{d.label}</b>
                <small>
                  {d.os} · {d.last_seen_at ? t.cloud.lastSeen(relativeTime(d.last_seen_at, lang)) : "—"}
                </small>
              </span>
              <button className="btn-quiet" onClick={() => window.confirm(t.cloud.removeConfirm(d.label)) && run(() => api.removeDevice(d.id))}>
                {t.cloud.remove}
              </button>
            </li>
          ))}
        </ul>
      </section>

      <section className="acct-sec">
        <h3 className="lab">{t.cloud.passkeys}</h3>
        <ul className="acct-list">
          {passkeys?.map((p, i) => (
            <li key={p.id}>
              <span className="nm">
                <b className="pix">PK{String(i + 1).padStart(2, "0")}</b>
                <small>
                  {t.cloud.passkeyAdded(relativeTime(p.created_at, lang))}
                  {p.last_used_at ? ` · ${relativeTime(p.last_used_at, lang)}` : ""}
                </small>
              </span>
              {passkeys.length > 1 && (
                <button className="btn-quiet" onClick={() => run(() => api.removePasskey(p.id))}>
                  {t.cloud.remove}
                </button>
              )}
            </li>
          ))}
        </ul>
        <button className="btn-quiet" onClick={() => run(() => api.addPasskey())}>
          + {t.cloud.addPasskey}
        </button>
      </section>

      {user.admin && (
        <section className="acct-sec">
          <h3 className="lab">{t.cloud.invite}</h3>
          <p className="solo-note">{t.cloud.inviteHint}</p>
          {invite ? (
            <div className="copy-row">
              <input readOnly value={invite} onFocus={(e) => e.target.select()} />
              <button
                className="btn-quiet"
                onClick={() => navigator.clipboard.writeText(invite).then(() => setCopied(true))}
              >
                {copied ? t.cloud.copied : t.cloud.copy}
              </button>
            </div>
          ) : (
            <button className="btn-quiet" onClick={() => run(async () => setInvite((await api.invite()).url))}>
              + {t.cloud.inviteMake}
            </button>
          )}
        </section>
      )}

      <section className="acct-sec acct-foot">
        {error && <p className="solo-err">{error}</p>}
        <button className="btn-quiet" onClick={() => run(async () => (await api.signOut(), onSignedOut()))}>
          {t.cloud.signOut}
        </button>
      </section>
      <GlassLayers />
    </dialog>
  );
}
