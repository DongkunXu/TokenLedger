import { startAuthentication, startRegistration } from "@simplewebauthn/browser";
import type { CaptureOutcome, Device, LinkRequest, PasskeyInfo, Session, View } from "./types";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`/api/${path}`, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: "same-origin",
  });
  if (!res.ok) {
    const text = await res.text();
    let message = text || `${res.status}`;
    try {
      message = JSON.parse(text).error ?? message;
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, message);
  }
  return res.json() as Promise<T>;
}

const get = <T>(path: string) => call<T>("GET", path);
const post = <T>(path: string, body: unknown = {}) => call<T>("POST", path, body);

export const api = {
  session: () => get<Session>("session"),
  view: (range: string) => get<View>(`view?range=${range}`),
  capture: () => post<CaptureOutcome[]>("capture"),

  // the cloud dashboard
  signIn: async () => {
    const start = await post<{ challenge_id: string; options: Parameters<typeof startAuthentication>[0]["optionsJSON"] }>(
      "passkey/login/options",
    );
    const response = await startAuthentication({ optionsJSON: start.options });
    return post<{ ok: true }>("passkey/login/verify", { challenge_id: start.challenge_id, response });
  },
  // A new account (setup or invite code with a name), a lost account back (recover code), or
  // one more passkey for the signed-in person (neither).
  addPasskey: async (code?: string, name?: string) => {
    const start = await post<{ challenge_id: string; options: Parameters<typeof startRegistration>[0]["optionsJSON"] }>(
      "passkey/register/options",
      { code, name },
    );
    const response = await startRegistration({ optionsJSON: start.options });
    return post<{ ok: true; name: string }>("passkey/register/verify", { challenge_id: start.challenge_id, response });
  },
  signOut: () => post<{ ok: true }>("logout"),
  passkeys: () => get<{ passkeys: PasskeyInfo[] }>("passkeys"),
  removePasskey: (id: string) => call<{ ok: true }>("DELETE", `passkeys/${id}`),
  devices: () => get<{ devices: Device[] }>("devices"),
  removeDevice: (id: string) => call<{ ok: true }>("DELETE", `devices/${id}`),
  invite: () => post<{ url: string; expires_in_days: number }>("invites"),
  linkRequest: (code: string) => get<LinkRequest>(`link/request?code=${encodeURIComponent(code)}`),
  approveLink: (code: string) => post<{ ok: true; machine: string }>("link/approve", { code }),
  denyLink: (code: string) => post<{ ok: true }>("link/deny", { code }),
};
