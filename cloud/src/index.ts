// TokenLedger cloud ledger: a Cloudflare Worker with a D1 database.
//
// * machines sign in and share their snapshots and dashboard (sync.ts)
// * people sign in with passkeys and read their dashboard (auth.ts, sync.ts)
// * everything else is the dashboard's static files (the same build as the local one)
//
// Every response carries strict security headers. Requests that change something with a
// session cookie must come from this origin; machines use bearer tokens instead.

import { routeAuth } from "./auth";
import { routeSync } from "./sync";
import { type Env, HttpError, json } from "./util";

const SECURITY_HEADERS: Record<string, string> = {
  "Content-Security-Policy":
    "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; font-src 'self'; connect-src 'self'; " +
    "script-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'",
  "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
  "X-Content-Type-Options": "nosniff",
  "X-Frame-Options": "DENY",
  "Referrer-Policy": "no-referrer",
  "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
  "Cross-Origin-Opener-Policy": "same-origin",
  "Cross-Origin-Resource-Policy": "same-origin",
};

function secured(response: Response): Response {
  const out = new Response(response.body, response);
  for (const [k, v] of Object.entries(SECURITY_HEADERS)) out.headers.set(k, v);
  return out;
}

async function api(request: Request, env: Env, url: URL): Promise<Response> {
  const changing = !["GET", "HEAD", "OPTIONS"].includes(request.method);
  if (changing && request.headers.has("Cookie") && request.headers.get("Origin") !== url.origin) {
    throw new HttpError(403, "cross-site request refused");
  }
  return (
    (await routeAuth(request, env, url.pathname)) ??
    (await routeSync(request, env, url.pathname)) ??
    json({ error: "not found" }, 404)
  );
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    if (!url.pathname.startsWith("/api/")) return secured(await env.ASSETS.fetch(request));
    try {
      return secured(await api(request, env, url));
    } catch (e) {
      if (e instanceof HttpError) return secured(json({ error: e.message }, e.status));
      console.error(e);
      return secured(json({ error: "internal error" }, 500));
    }
  },
} satisfies ExportedHandler<Env>;
