// Small helpers shared by the routes.

export interface Env {
  DB: D1Database;
  ASSETS: Fetcher;
  GOOGLE_CLIENT_ID?: string; // set: people sign in with Google (google.ts)
  // Only for tests against a stand-in for Google:
  GOOGLE_AUTH_URL?: string;
  GOOGLE_JWKS_URL?: string;
  GOOGLE_ISSUER?: string;
}

export class HttpError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

export function json(data: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", ...headers },
  });
}

export function now(): string {
  return new Date().toISOString();
}

export function later(seconds: number): string {
  return new Date(Date.now() + seconds * 1000).toISOString();
}

const B64URL = /[+/=]/g;

export function base64url(bytes: Uint8Array): string {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(B64URL, (c) => (c === "+" ? "-" : c === "/" ? "_" : ""));
}

export function randomToken(bytes = 32): string {
  return base64url(crypto.getRandomValues(new Uint8Array(bytes)));
}

// Easy to read aloud and type: no 0/O, 1/I/L.
const CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789";

export function userCode(): string {
  const raw = crypto.getRandomValues(new Uint8Array(8));
  const chars = [...raw].map((b) => CODE_ALPHABET[b % CODE_ALPHABET.length]);
  return `${chars.slice(0, 4).join("")}-${chars.slice(4).join("")}`;
}

export async function sha256(text: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export async function readJson<T>(request: Request, limit = 64 * 1024): Promise<T> {
  const body = await readBytes(request, limit);
  try {
    return JSON.parse(new TextDecoder().decode(body)) as T;
  } catch {
    throw new HttpError(400, "request body is not JSON");
  }
}

export async function readBytes(request: Request, limit: number): Promise<Uint8Array> {
  const declared = Number(request.headers.get("Content-Length") ?? "0");
  if (declared > limit) throw new HttpError(413, `request body larger than ${limit} bytes`);
  const body = new Uint8Array(await request.arrayBuffer());
  if (body.length > limit) throw new HttpError(413, `request body larger than ${limit} bytes`);
  return body;
}

export function str(value: unknown, name: string, max = 200): string {
  if (typeof value !== "string" || !value.trim() || value.length > max) throw new HttpError(400, `${name} is missing or too long`);
  return value.trim();
}

// -- blobs: bytes split into parts below D1's row size limit ----------------------------------

const PART = 1_000_000;

export function putBlob(db: D1Database, user: string, kind: string, name: string, data: Uint8Array): D1PreparedStatement[] {
  const statements = [db.prepare("DELETE FROM blobs WHERE user_id = ? AND kind = ? AND name = ?").bind(user, kind, name)];
  for (let part = 0, at = 0; at < data.length || part === 0; part++, at += PART) {
    statements.push(
      db
        .prepare("INSERT INTO blobs(user_id, kind, name, part, data) VALUES(?, ?, ?, ?, ?)")
        .bind(user, kind, name, part, data.subarray(at, at + PART)),
    );
  }
  return statements;
}

export async function getBlob(db: D1Database, user: string, kind: string, name: string): Promise<Uint8Array | null> {
  const { results } = await db
    .prepare("SELECT data FROM blobs WHERE user_id = ? AND kind = ? AND name = ? ORDER BY part")
    .bind(user, kind, name)
    .all<{ data: ArrayBuffer | number[] }>();
  if (!results.length) return null;
  const parts = results.map((r) => (r.data instanceof ArrayBuffer ? new Uint8Array(r.data) : Uint8Array.from(r.data)));
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let at = 0;
  for (const p of parts) {
    out.set(p, at);
    at += p.length;
  }
  return out;
}
