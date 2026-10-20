// People and their sign-in: passkeys, sessions, one-time codes (Google: google.ts).
//
// There are no passwords. A person signs in with Google when the deployment has a Google
// client id, or with a passkey (Touch ID, Face ID, Windows Hello, a phone or a security key). The first account is created with a setup code
// printed by `scripts/cloud.sh deploy`. Only when the deployment switches invitations on
// (INVITES = "on") can that admin invite others, each with a ledger of their own.
// A person who lost every way to sign in gets a recovery code from whoever runs the deployment
// (`scripts/cloud.sh recover <name>`), which lets them add a new one.

import {
  generateAuthenticationOptions,
  generateRegistrationOptions,
  verifyAuthenticationResponse,
  verifyRegistrationResponse,
} from "@simplewebauthn/server";
import type { AuthenticationResponseJSON, RegistrationResponseJSON } from "@simplewebauthn/server";
import { type Env, HttpError, json, later, now, randomToken, readJson, sha256, str } from "./util";

const SESSION_COOKIE = "__Host-tl_session";
const SESSION_DAYS = 30;
const CHALLENGE_SECONDS = 300;
const INVITE_DAYS = 7;
const NAME = /^[\p{L}\p{N}][\p{L}\p{N} ._-]{0,39}$/u;

export interface User {
  id: string;
  name: string;
  admin: number;
  timezone: string | null;
  grace_days: number | null;
}

interface Code {
  id: string;
  kind: "setup" | "invite" | "recover";
  user_id: string | null;
}

// -- sessions ---------------------------------------------------------------------------------

export async function sessionUser(request: Request, env: Env): Promise<User | null> {
  const cookie = request.headers.get("Cookie") ?? "";
  const value = cookie
    .split(";")
    .map((c) => c.trim())
    .find((c) => c.startsWith(`${SESSION_COOKIE}=`))
    ?.slice(SESSION_COOKIE.length + 1);
  if (!value) return null;
  return env.DB.prepare(
    "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.id = ? AND s.expires_at > ?",
  )
    .bind(await sha256(value), now())
    .first<User>();
}

export async function requireUser(request: Request, env: Env): Promise<User> {
  const user = await sessionUser(request, env);
  if (!user) throw new HttpError(401, "sign in first");
  return user;
}

export async function startSession(env: Env, user: string): Promise<string> {
  const value = randomToken();
  await env.DB.prepare("INSERT INTO sessions(id, user_id, created_at, expires_at) VALUES(?, ?, ?, ?)")
    .bind(await sha256(value), user, now(), later(SESSION_DAYS * 86400))
    .run();
  return `${SESSION_COOKIE}=${value}; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=${SESSION_DAYS * 86400}`;
}

const CLEAR_COOKIE = `${SESSION_COOKIE}=; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=0`;

export async function forgetExpired(env: Env): Promise<void> {
  const t = now();
  await env.DB.batch([
    env.DB.prepare("DELETE FROM sessions WHERE expires_at <= ?").bind(t),
    env.DB.prepare("DELETE FROM challenges WHERE expires_at <= ?").bind(t),
    env.DB.prepare("DELETE FROM codes WHERE expires_at <= ? OR used_at IS NOT NULL").bind(t),
    env.DB.prepare("DELETE FROM links WHERE expires_at <= ?").bind(t),
    env.DB.prepare("DELETE FROM oauth_states WHERE expires_at <= ?").bind(t),
  ]);
}

// -- codes ------------------------------------------------------------------------------------

async function findCode(env: Env, code: string): Promise<Code> {
  const found = await env.DB.prepare(
    "SELECT id, kind, user_id FROM codes WHERE id = ? AND used_at IS NULL AND expires_at > ?",
  )
    .bind(await sha256(code.trim()), now())
    .first<Code>();
  if (!found) throw new HttpError(403, "this link has expired or was already used");
  return found;
}

// -- enrolling: a new account, a lost account back, one more sign-in method ----------------------

// Who a passkey or Google account is being added to: the signed-in person, or the account a
// one-time code creates (setup, invite) or recovers.
// A deployment is one person's unless invitations are switched on explicitly.
export function invitesEnabled(env: Env): boolean {
  return env.INVITES === "on";
}

export interface Enrollment {
  user_id: string;
  name: string;
  new_user: boolean;
  code_id: string | null;
  admin: boolean;
}

export async function enrollment(env: Env, current: User | null, body: { code?: string; name?: string }): Promise<Enrollment> {
  if (current) {
    return { user_id: current.id, name: current.name, new_user: false, code_id: null, admin: !!current.admin };
  }
  const code = await findCode(env, str(body.code, "code"));
  if (code.kind === "invite" && !invitesEnabled(env)) throw new HttpError(403, "invitations are off for this ledger");
  if (code.kind === "recover") {
    const user = await env.DB.prepare("SELECT * FROM users WHERE id = ?").bind(code.user_id).first<User>();
    if (!user) throw new HttpError(403, "this link has expired or was already used");
    return { user_id: user.id, name: user.name, new_user: false, code_id: code.id, admin: !!user.admin };
  }
  const name = str(body.name, "name", 40);
  if (!NAME.test(name)) throw new HttpError(400, "use letters, digits, spaces, dots, dashes or underscores");
  if (code.kind === "setup" && (await env.DB.prepare("SELECT 1 FROM users LIMIT 1").first())) {
    throw new HttpError(403, "this ledger is already set up");
  }
  if (await env.DB.prepare("SELECT 1 FROM users WHERE name = ?").bind(name).first()) {
    throw new HttpError(409, "that name is taken");
  }
  return { user_id: randomToken(16), name, new_user: true, code_id: code.id, admin: code.kind === "setup" };
}

// Spent before anything is created: of two attempts with one code, only one gets it.
export async function spendCode(env: Env, data: Enrollment, t: string): Promise<void> {
  if (!data.code_id) return;
  const spent = await env.DB.prepare(
    "UPDATE codes SET used_at = ? WHERE id = ? AND used_at IS NULL AND expires_at > ? RETURNING id",
  )
    .bind(t, data.code_id, t)
    .first();
  if (!spent) throw new HttpError(403, "this link has expired or was already used");
}

export function newUser(env: Env, data: Enrollment, t: string): D1PreparedStatement[] {
  if (!data.new_user) return [];
  return [
    env.DB.prepare("INSERT INTO users(id, name, admin, created_at) VALUES(?, ?, ?, ?)").bind(
      data.user_id, data.name, data.admin ? 1 : 0, t,
    ),
  ];
}

// -- routes -----------------------------------------------------------------------------------

export async function routeAuth(request: Request, env: Env, path: string): Promise<Response | null> {
  const url = new URL(request.url);
  const rpID = url.hostname;
  const origin = url.origin;
  const method = request.method;

  if (path === "/api/session" && method === "GET") {
    const user = await sessionUser(request, env);
    const anyone = await env.DB.prepare("SELECT 1 FROM users LIMIT 1").first();
    return json({
      mode: "cloud",
      user: user ? { name: user.name, admin: !!user.admin } : null,
      setup_needed: !anyone,
      google: !!env.GOOGLE_CLIENT_ID,
      invites: invitesEnabled(env),
    });
  }

  if (path === "/api/passkey/register/options" && method === "POST") {
    const body = await readJson<{ code?: string; name?: string }>(request);
    const data = await enrollment(env, await sessionUser(request, env), body);
    const existing = await env.DB.prepare("SELECT id, transports FROM passkeys WHERE user_id = ?")
      .bind(data.user_id)
      .all<{ id: string; transports: string | null }>();
    const options = await generateRegistrationOptions({
      rpName: "TokenLedger",
      rpID,
      userName: data.name,
      userID: new Uint8Array(new TextEncoder().encode(data.user_id)),
      attestationType: "none",
      excludeCredentials: existing.results.map((p) => ({ id: p.id, transports: p.transports ? JSON.parse(p.transports) : undefined })),
      authenticatorSelection: { residentKey: "required", userVerification: "preferred" },
    });
    const id = randomToken(16);
    await forgetExpired(env);
    await env.DB.prepare("INSERT INTO challenges(id, kind, challenge, data, expires_at) VALUES(?, 'register', ?, ?, ?)")
      .bind(id, options.challenge, JSON.stringify(data), later(CHALLENGE_SECONDS))
      .run();
    return json({ challenge_id: id, options });
  }

  if (path === "/api/passkey/register/verify" && method === "POST") {
    const body = await readJson<{ challenge_id: string; response: RegistrationResponseJSON }>(request);
    const challenge = await takeChallenge(env, str(body.challenge_id, "challenge_id"), "register");
    const data = JSON.parse(challenge.data ?? "{}") as Enrollment;
    const current = await sessionUser(request, env);
    if (!data.code_id && current?.id !== data.user_id) throw new HttpError(401, "sign in first");
    const verification = await verifyRegistrationResponse({
      response: body.response,
      expectedChallenge: challenge.challenge,
      expectedOrigin: origin,
      expectedRPID: rpID,
      requireUserVerification: false,
    }).catch((e: Error) => {
      throw new HttpError(400, `passkey not accepted: ${e.message}`);
    });
    if (!verification.verified) throw new HttpError(400, "passkey not accepted");
    const credential = verification.registrationInfo.credential;
    const t = now();
    await spendCode(env, data, t);
    const statements = newUser(env, data, t);
    statements.push(
      env.DB.prepare(
        "INSERT INTO passkeys(id, user_id, public_key, counter, transports, created_at) VALUES(?, ?, ?, ?, ?, ?)",
      ).bind(credential.id, data.user_id, credential.publicKey, credential.counter, JSON.stringify(credential.transports ?? []), t),
    );
    await env.DB.batch(statements).catch((e: Error) => {
      throw new HttpError(409, /UNIQUE/.test(e.message) ? "that name is taken" : `could not save the passkey: ${e.message}`);
    });
    return json({ ok: true, name: data.name }, 200, { "Set-Cookie": await startSession(env, data.user_id) });
  }

  if (path === "/api/passkey/login/options" && method === "POST") {
    const options = await generateAuthenticationOptions({ rpID, userVerification: "preferred" });
    const id = randomToken(16);
    await forgetExpired(env);
    await env.DB.prepare("INSERT INTO challenges(id, kind, challenge, expires_at) VALUES(?, 'login', ?, ?)")
      .bind(id, options.challenge, later(CHALLENGE_SECONDS))
      .run();
    return json({ challenge_id: id, options });
  }

  if (path === "/api/passkey/login/verify" && method === "POST") {
    const body = await readJson<{ challenge_id: string; response: AuthenticationResponseJSON }>(request);
    const challenge = await takeChallenge(env, str(body.challenge_id, "challenge_id"), "login");
    const passkey = await env.DB.prepare("SELECT * FROM passkeys WHERE id = ?")
      .bind(str(body.response?.id, "credential id", 1024))
      .first<{ id: string; user_id: string; public_key: ArrayBuffer | number[]; counter: number; transports: string | null }>();
    if (!passkey) throw new HttpError(403, "this passkey is not registered here");
    const publicKey = passkey.public_key instanceof ArrayBuffer ? new Uint8Array(passkey.public_key) : Uint8Array.from(passkey.public_key);
    const verification = await verifyAuthenticationResponse({
      response: body.response,
      expectedChallenge: challenge.challenge,
      expectedOrigin: origin,
      expectedRPID: rpID,
      credential: {
        id: passkey.id,
        publicKey,
        counter: passkey.counter,
        transports: passkey.transports ? JSON.parse(passkey.transports) : undefined,
      },
      requireUserVerification: false,
    }).catch((e: Error) => {
      throw new HttpError(403, `passkey not accepted: ${e.message}`);
    });
    if (!verification.verified) throw new HttpError(403, "passkey not accepted");
    await env.DB.prepare("UPDATE passkeys SET counter = ?, last_used_at = ? WHERE id = ?")
      .bind(verification.authenticationInfo.newCounter, now(), passkey.id)
      .run();
    return json({ ok: true }, 200, { "Set-Cookie": await startSession(env, passkey.user_id) });
  }

  if (path === "/api/logout" && method === "POST") {
    const cookie = (request.headers.get("Cookie") ?? "").match(new RegExp(`${SESSION_COOKIE}=([^;]+)`));
    if (cookie) await env.DB.prepare("DELETE FROM sessions WHERE id = ?").bind(await sha256(cookie[1])).run();
    return json({ ok: true }, 200, { "Set-Cookie": CLEAR_COOKIE });
  }

  if (path === "/api/passkeys" && method === "GET") {
    const user = await requireUser(request, env);
    const { results } = await env.DB.prepare(
      "SELECT id, created_at, last_used_at FROM passkeys WHERE user_id = ? ORDER BY created_at",
    )
      .bind(user.id)
      .all();
    return json({ passkeys: results });
  }

  const passkeyMatch = path.match(/^\/api\/passkeys\/([A-Za-z0-9_-]{1,1024})$/);
  if (passkeyMatch && method === "DELETE") {
    const user = await requireUser(request, env);
    const count = await env.DB.prepare(
      "SELECT (SELECT COUNT(*) FROM passkeys WHERE user_id = ?1) + (SELECT COUNT(*) FROM identities WHERE user_id = ?1) AS n",
    )
      .bind(user.id)
      .first<{ n: number }>();
    if ((count?.n ?? 0) <= 1) throw new HttpError(409, "this is your only way to sign in; add another one first");
    await env.DB.prepare("DELETE FROM passkeys WHERE id = ? AND user_id = ?").bind(passkeyMatch[1], user.id).run();
    return json({ ok: true });
  }

  if (path === "/api/invites" && method === "POST") {
    const user = await requireUser(request, env);
    if (!invitesEnabled(env)) throw new HttpError(403, "invitations are off for this ledger");
    if (!user.admin) throw new HttpError(403, "only an admin can invite");
    const code = randomToken(24);
    await env.DB.prepare("INSERT INTO codes(id, kind, user_id, created_at, expires_at) VALUES(?, 'invite', ?, ?, ?)")
      .bind(await sha256(code), user.id, now(), later(INVITE_DAYS * 86400))
      .run();
    return json({ url: `${origin}/join#${code}`, expires_in_days: INVITE_DAYS });
  }

  return null;
}

async function takeChallenge(env: Env, id: string, kind: string): Promise<{ challenge: string; data: string | null }> {
  // Deleted as it is read: a challenge answers one ceremony only.
  const row = await env.DB.prepare(
    "DELETE FROM challenges WHERE id = ? AND kind = ? AND expires_at > ? RETURNING challenge, data",
  )
    .bind(id, kind, now())
    .first<{ challenge: string; data: string | null }>();
  if (!row) throw new HttpError(400, "this sign-in attempt expired; try again");
  return row;
}
