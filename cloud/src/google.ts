// Signing in with Google (OpenID Connect).
//
// Only Google accounts linked to a person here can sign in: the first account links its
// Google account with the setup link, invited people with their invitation, and a
// signed-in person can link one more from the account menu. A recovery link links a new
// Google account to an existing person.
//
// The flow needs only a public client id, no client secret: Google posts an ID token back
// (response_type=id_token, response_mode=form_post), and its signature is checked against
// Google's published keys. The answer must carry the state of a sign-in this browser
// started (bound to a short-lived cookie) and that sign-in's nonce inside the token.

import { type Enrollment, enrollment, forgetExpired, newUser, sessionUser, spendCode, startSession } from "./auth";
import { type Env, HttpError, json, later, now, randomToken, readJson, sha256 } from "./util";

const AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth";
const JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs";
const ISSUERS = ["https://accounts.google.com", "accounts.google.com"];
const STATE_COOKIE = "__Host-tl_oauth";
const STATE_SECONDS = 600;
export const CALLBACK = "/api/auth/google/callback";

type Purpose = "login" | "enroll";

interface Pending {
  purpose: Purpose;
  enrollment: Enrollment | null;
  next: string; // where the dashboard continues afterwards (a path on this site)
}

const NEXT = /^\/(?!\/)[A-Za-z0-9/_?=&.%-]{0,200}$/;

interface Claims {
  iss: string;
  aud: string;
  sub: string;
  exp: number;
  nonce?: string;
  email?: string;
  email_verified?: boolean;
}

export function googleEnabled(env: Env): boolean {
  return !!env.GOOGLE_CLIENT_ID;
}

export async function routeGoogle(request: Request, env: Env, path: string): Promise<Response | null> {
  const method = request.method;
  if (path === "/api/auth/google/start" && method === "POST") return start(request, env);
  if (path === CALLBACK && method === "POST") return callback(request, env);
  if (path === "/api/identities" && method === "GET") {
    const user = await sessionUser(request, env);
    if (!user) throw new HttpError(401, "sign in first");
    const { results } = await env.DB.prepare(
      "SELECT provider, email, created_at FROM identities WHERE user_id = ? ORDER BY created_at",
    )
      .bind(user.id)
      .all();
    return json({ identities: results });
  }
  return null;
}

// The browser asks to sign in (no code), to create or recover an account (a code), or, signed
// in, to link a Google account; it gets the address to send the person to.
async function start(request: Request, env: Env): Promise<Response> {
  if (!googleEnabled(env)) throw new HttpError(404, "Google sign-in is not set up here");
  const body = await readJson<{ code?: string; name?: string; next?: string }>(request);
  const current = await sessionUser(request, env);
  const next = typeof body.next === "string" && NEXT.test(body.next) ? body.next : "/";
  const pending: Pending =
    current || body.code
      ? { purpose: "enroll", enrollment: await enrollment(env, current, body), next }
      : { purpose: "login", enrollment: null, next };
  const cookie = randomToken();
  const state = randomToken();
  const nonce = randomToken();
  await forgetExpired(env);
  await env.DB.prepare("INSERT INTO oauth_states(id, state, nonce, data, expires_at) VALUES(?, ?, ?, ?, ?)")
    .bind(await sha256(cookie), state, nonce, JSON.stringify(pending), later(STATE_SECONDS))
    .run();
  const origin = new URL(request.url).origin;
  const target = new URL(env.GOOGLE_AUTH_URL || AUTH_URL);
  target.search = new URLSearchParams({
    client_id: env.GOOGLE_CLIENT_ID!,
    redirect_uri: origin + CALLBACK,
    response_type: "id_token",
    response_mode: "form_post",
    scope: "openid email",
    state,
    nonce,
    prompt: "select_account",
  }).toString();
  // SameSite=None: Google's answer arrives as a cross-site POST, which carries no Lax cookie.
  const set = `${STATE_COOKIE}=${cookie}; Path=/; Secure; HttpOnly; SameSite=None; Max-Age=${STATE_SECONDS}`;
  return json({ url: target.toString() }, 200, { "Set-Cookie": set });
}

async function callback(request: Request, env: Env): Promise<Response> {
  try {
    const form = await request.formData();
    const token = String(form.get("id_token") ?? "");
    const state = String(form.get("state") ?? "");
    const cookie = readCookie(request, STATE_COOKIE);
    if (!token || !state || !cookie) throw new HttpError(400, "the sign-in answer is incomplete; try again");
    const row = await env.DB.prepare(
      "DELETE FROM oauth_states WHERE id = ? AND expires_at > ? RETURNING state, nonce, data",
    )
      .bind(await sha256(cookie), now())
      .first<{ state: string; nonce: string; data: string }>();
    if (!row || row.state !== state) throw new HttpError(400, "this sign-in expired or was started elsewhere; try again");
    const claims = await verifyIdToken(env, token);
    if (claims.nonce !== row.nonce) throw new HttpError(400, "the sign-in answer does not belong to this attempt");
    const pending = JSON.parse(row.data) as Pending;
    const user = pending.purpose === "login" ? await linkedUser(env, claims) : await link(env, pending.enrollment!, claims);
    return finish(pending.next || "/", [await startSession(env, user), clearState()]);
  } catch (e) {
    const message = e instanceof HttpError ? e.message : "Google sign-in failed";
    if (!(e instanceof HttpError)) console.error(e);
    return finish(`/?signin_error=${encodeURIComponent(message)}`, [clearState()]);
  }
}

async function linkedUser(env: Env, claims: Claims): Promise<string> {
  const found = await env.DB.prepare("SELECT user_id FROM identities WHERE provider = 'google' AND subject = ?")
    .bind(claims.sub)
    .first<{ user_id: string }>();
  if (!found) throw new HttpError(403, `${claims.email ?? "this Google account"} is not linked to an account here`);
  return found.user_id;
}

async function link(env: Env, data: Enrollment, claims: Claims): Promise<string> {
  const taken = await env.DB.prepare("SELECT user_id FROM identities WHERE provider = 'google' AND subject = ?")
    .bind(claims.sub)
    .first<{ user_id: string }>();
  if (taken && taken.user_id !== data.user_id) throw new HttpError(409, "this Google account is linked to another account here");
  if (taken) return data.user_id;
  const t = now();
  await spendCode(env, data, t);
  await env.DB.batch([
    ...newUser(env, data, t),
    env.DB.prepare("INSERT INTO identities(provider, subject, user_id, email, created_at) VALUES('google', ?, ?, ?, ?)").bind(
      claims.sub, data.user_id, claims.email ?? null, t,
    ),
  ]).catch((e: Error) => {
    throw new HttpError(409, /users\.name|UNIQUE constraint failed: users/.test(e.message) ? "that name is taken" : `could not link the account: ${e.message}`);
  });
  return data.user_id;
}

// -- the ID token -------------------------------------------------------------------------------

async function verifyIdToken(env: Env, token: string): Promise<Claims> {
  const parts = token.split(".");
  if (parts.length !== 3) throw new HttpError(400, "not an ID token");
  const header = JSON.parse(text(parts[0])) as { alg: string; kid: string };
  if (header.alg !== "RS256") throw new HttpError(400, "unexpected ID token algorithm");
  const jwk = (await keys(env)).find((k) => k.kid === header.kid);
  if (!jwk) throw new HttpError(400, "the ID token is signed with an unknown key");
  const key = await crypto.subtle.importKey("jwk", jwk, { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["verify"]);
  const signed = new TextEncoder().encode(`${parts[0]}.${parts[1]}`);
  if (!(await crypto.subtle.verify("RSASSA-PKCS1-v1_5", key, bytes(parts[2]), signed))) {
    throw new HttpError(400, "the ID token's signature is invalid");
  }
  const claims = JSON.parse(text(parts[1])) as Claims;
  const issuers = env.GOOGLE_ISSUER ? [env.GOOGLE_ISSUER] : ISSUERS;
  if (!issuers.includes(claims.iss)) throw new HttpError(400, "the ID token is not from Google");
  if (claims.aud !== env.GOOGLE_CLIENT_ID) throw new HttpError(400, "the ID token is for another application");
  if (claims.exp * 1000 < Date.now()) throw new HttpError(400, "the ID token has expired; try again");
  if (claims.email && claims.email_verified === false) throw new HttpError(403, "this Google account's email is not verified");
  return claims;
}

let cachedKeys: { keys: (JsonWebKey & { kid: string })[]; until: number } | null = null;

async function keys(env: Env): Promise<(JsonWebKey & { kid: string })[]> {
  if (cachedKeys && cachedKeys.until > Date.now()) return cachedKeys.keys;
  const res = await fetch(env.GOOGLE_JWKS_URL || JWKS_URL);
  if (!res.ok) throw new Error(`Google's keys: HTTP ${res.status}`);
  const age = Number(/max-age=(\d+)/.exec(res.headers.get("Cache-Control") ?? "")?.[1] ?? "3600");
  cachedKeys = { keys: ((await res.json()) as { keys: (JsonWebKey & { kid: string })[] }).keys, until: Date.now() + age * 1000 };
  return cachedKeys.keys;
}

function bytes(b64url: string): Uint8Array {
  const b64 = b64url.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (b64url.length % 4)) % 4);
  return Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
}

function text(b64url: string): string {
  return new TextDecoder().decode(bytes(b64url));
}

// -- answering the browser ------------------------------------------------------------------------

function readCookie(request: Request, name: string): string | null {
  const found = (request.headers.get("Cookie") ?? "")
    .split(";")
    .map((c) => c.trim())
    .find((c) => c.startsWith(`${name}=`));
  return found ? found.slice(name.length + 1) : null;
}

function clearState(): string {
  return `${STATE_COOKIE}=; Path=/; Secure; HttpOnly; SameSite=None; Max-Age=0`;
}

// A page that moves on by itself. Arriving from Google is a cross-site navigation, which
// carries no SameSite=Strict cookie; the dashboard is opened by this page instead, so the new
// session cookie comes along.
function finish(to: string, cookies: string[]): Response {
  const safe = to.replace(/[&"<>]/g, (c) => `&#${c.charCodeAt(0)};`);
  const html = `<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0;url=${safe}"><title>TokenLedger</title>`;
  const headers = new Headers({ "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" });
  for (const c of cookies) headers.append("Set-Cookie", c);
  return new Response(html, { headers });
}
