// Machines: signing them in, and the snapshots and dashboards they share.
//
// Signing a machine in follows the device authorization pattern: the machine asks for a
// code (`/api/link/start`), the person approves it on the web while signed in, and the
// machine, polling, receives its own device token once. Every later request of that
// machine carries the token; the cloud checks that it still comes from the same machine
// id, the same ledger (instance) and the same computer (host).
//
// The cloud never reads what machines share: a snapshot or a dashboard range is stored
// and handed out as the compressed bytes it arrived as.

import { requireUser, type User } from "./auth";
import { type Env, HttpError, getBlob, json, later, now, putBlob, randomToken, readBytes, readJson, sha256, str, userCode } from "./util";

const LINK_SECONDS = 600;
const POLL_SECONDS = 3;
const MAX_WAITING_LINKS = 100;
const MAX_SNAPSHOT = 16 * 1024 * 1024;
const MAX_DASHBOARD = 4 * 1024 * 1024;
const RANGES = ["d30", "d90", "d180", "y1", "all"];
const DASHBOARD_MAGIC = "TLDASH1\n";
const TIME = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$/;

interface LinkRequest {
  machine: { id: string; label: string; os: string };
  instance: string;
  host: string;
  app_version: string;
  timezone: string;
  grace_days: number;
}

interface Device {
  id: string;
  user_id: string;
  machine: string;
  label: string;
  os: string;
  instance: string;
  host: string;
}

function linkRequest(body: unknown): LinkRequest {
  const b = body as Partial<LinkRequest>;
  const machine = b.machine ?? ({} as LinkRequest["machine"]);
  const grace = Number(b.grace_days);
  if (!Number.isInteger(grace) || grace < 1 || grace > 60) throw new HttpError(400, "grace_days is invalid");
  return {
    machine: { id: str(machine.id, "machine id", 64), label: str(machine.label, "machine label", 100), os: str(machine.os, "os", 20) },
    instance: str(b.instance, "instance", 64),
    host: typeof b.host === "string" ? b.host.slice(0, 64) : "",
    app_version: typeof b.app_version === "string" ? b.app_version.slice(0, 40) : "",
    timezone: str(b.timezone, "timezone", 64),
    grace_days: grace,
  };
}

function whereFrom(request: Request): string {
  const cf = (request as Request & { cf?: { city?: string; country?: string } }).cf;
  return [cf?.city, cf?.country].filter(Boolean).join(", ");
}

async function device(request: Request, env: Env): Promise<Device> {
  const auth = request.headers.get("Authorization") ?? "";
  if (!auth.startsWith("Bearer ")) throw new HttpError(401, "this machine is not signed in");
  const found = await env.DB.prepare("SELECT * FROM devices WHERE token = ? AND revoked_at IS NULL")
    .bind(await sha256(auth.slice(7).trim()))
    .first<Device>();
  if (!found) throw new HttpError(401, "this machine was signed out of the cloud ledger");
  if (request.headers.get("X-TokenLedger-Machine") !== found.machine) {
    throw new HttpError(403, "this device token belongs to another machine id");
  }
  const host = request.headers.get("X-TokenLedger-Host") ?? "";
  if (found.host && host !== found.host) {
    throw new HttpError(403, "this device token belongs to another computer; was the data folder copied? Sign this computer in on its own");
  }
  if (request.headers.get("X-TokenLedger-Instance") !== found.instance) {
    throw new HttpError(409, "this machine's ledger was recreated since it signed in; run `tokenledger sync login` again");
  }
  const version = (request.headers.get("X-TokenLedger-Version") ?? "").slice(0, 40) || null;
  await env.DB.prepare("UPDATE devices SET last_seen_at = ?, app_version = COALESCE(?, app_version) WHERE id = ?")
    .bind(now(), version, found.id)
    .run();
  return found;
}

async function owner(env: Env, id: string): Promise<User> {
  const user = await env.DB.prepare("SELECT * FROM users WHERE id = ?").bind(id).first<User>();
  if (!user) throw new HttpError(401, "this machine was signed out of the cloud ledger");
  return user;
}

function ledgerSettings(user: User) {
  return { timezone: user.timezone, grace_days: user.grace_days };
}

export async function routeSync(request: Request, env: Env, path: string): Promise<Response | null> {
  const method = request.method;
  const origin = new URL(request.url).origin;

  // -- signing a machine in ---------------------------------------------------------------

  if (path === "/api/link/start" && method === "POST") {
    const req = linkRequest(await readJson(request));
    // Anyone may ask; requests expire after ten minutes and only so many wait at once.
    await env.DB.prepare("DELETE FROM links WHERE expires_at <= ?").bind(now()).run();
    const waiting = await env.DB.prepare("SELECT COUNT(*) AS n FROM links").first<{ n: number }>();
    if ((waiting?.n ?? 0) >= MAX_WAITING_LINKS) throw new HttpError(429, "too many sign-in requests; try again in a few minutes");
    const code = randomToken();
    const user = userCode();
    await env.DB.prepare(
      "INSERT INTO links(id, user_code, request, origin, status, created_at, expires_at) VALUES(?, ?, ?, ?, 'pending', ?, ?)",
    )
      .bind(await sha256(code), user, JSON.stringify(req), whereFrom(request), now(), later(LINK_SECONDS))
      .run();
    return json({
      device_code: code,
      user_code: user,
      verify_url: `${origin}/link?code=${user}`,
      interval: POLL_SECONDS,
      expires_in: LINK_SECONDS,
    });
  }

  if (path === "/api/link/request" && method === "GET") {
    const user = await requireUser(request, env);
    const code = str(new URL(request.url).searchParams.get("code"), "code", 20).toUpperCase();
    const link = await env.DB.prepare("SELECT * FROM links WHERE user_code = ? AND status = 'pending' AND expires_at > ?")
      .bind(code, now())
      .first<{ request: string; origin: string; created_at: string }>();
    if (!link) throw new HttpError(404, "no sign-in request with this code; it may have expired");
    const req = JSON.parse(link.request) as LinkRequest;
    return json({
      machine: req.machine,
      app_version: req.app_version,
      from: link.origin,
      requested_at: link.created_at,
      replaces: await replaced(env, user, req),
      ledger: ledgerSettings(user),
      timezone: req.timezone,
    });
  }

  if ((path === "/api/link/approve" || path === "/api/link/deny") && method === "POST") {
    const user = await requireUser(request, env);
    const code = str((await readJson<{ code?: string }>(request)).code, "code", 20).toUpperCase();
    const link = await env.DB.prepare("SELECT * FROM links WHERE user_code = ? AND status = 'pending' AND expires_at > ?")
      .bind(code, now())
      .first<{ id: string; request: string }>();
    if (!link) throw new HttpError(404, "no sign-in request with this code; it may have expired");
    if (path === "/api/link/deny") {
      await env.DB.prepare("UPDATE links SET status = 'denied' WHERE id = ?").bind(link.id).run();
      return json({ ok: true });
    }
    const req = JSON.parse(link.request) as LinkRequest;
    await replaced(env, user, req); // refuses a different computer with the same machine id
    const statements = [env.DB.prepare("UPDATE links SET status = 'approved', user_id = ? WHERE id = ?").bind(user.id, link.id)];
    if (!user.timezone) {
      // The first machine decides how this ledger buckets days.
      statements.push(
        env.DB.prepare("UPDATE users SET timezone = ?, grace_days = ? WHERE id = ? AND timezone IS NULL").bind(
          req.timezone, req.grace_days, user.id,
        ),
      );
    }
    await env.DB.batch(statements);
    return json({ ok: true, machine: req.machine.label });
  }

  if (path === "/api/link/poll" && method === "POST") {
    const code = str((await readJson<{ device_code?: string }>(request)).device_code, "device_code", 100);
    const id = await sha256(code);
    const link = await env.DB.prepare("SELECT * FROM links WHERE id = ? AND expires_at > ?")
      .bind(id, now())
      .first<{ status: string; request: string; user_id: string | null }>();
    if (!link) return json({ error: "this sign-in request expired" }, 404);
    if (link.status === "pending") return json({ status: "pending" });
    await env.DB.prepare("DELETE FROM links WHERE id = ?").bind(id).run();
    if (link.status === "denied" || !link.user_id) return json({ status: "denied" });
    const user = await owner(env, link.user_id);
    const req = JSON.parse(link.request) as LinkRequest;
    const token = randomToken();
    const deviceId = randomToken(12);
    const t = now();
    await env.DB.batch([
      // The same computer signing in again (a new ledger there) replaces its old device.
      env.DB.prepare("UPDATE devices SET revoked_at = ? WHERE user_id = ? AND machine = ? AND revoked_at IS NULL").bind(
        t, user.id, req.machine.id,
      ),
      env.DB.prepare(
        "INSERT INTO devices(id, user_id, token, machine, label, os, instance, host, app_version, created_at, last_seen_at) " +
          "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
      ).bind(deviceId, user.id, await sha256(token), req.machine.id, req.machine.label, req.machine.os, req.instance, req.host,
        req.app_version, t, t),
    ]);
    return json({ status: "approved", token, device_id: deviceId, user: user.name, ledger: ledgerSettings(user) });
  }

  // -- devices, seen by their owner --------------------------------------------------------

  if (path === "/api/devices" && method === "GET") {
    const user = await requireUser(request, env);
    const { results } = await env.DB.prepare(
      "SELECT d.id, d.machine, d.label, d.os, d.app_version, d.created_at, d.last_seen_at, s.taken_at " +
        "FROM devices d LEFT JOIN snapshots s ON s.user_id = d.user_id AND s.machine = d.machine " +
        "WHERE d.user_id = ? AND d.revoked_at IS NULL ORDER BY d.created_at",
    )
      .bind(user.id)
      .all();
    return json({ devices: results });
  }

  const deviceMatch = path.match(/^\/api\/devices\/([A-Za-z0-9_-]{1,64})$/);
  if (deviceMatch && method === "DELETE") {
    const user = await requireUser(request, env);
    await env.DB.prepare("UPDATE devices SET revoked_at = ? WHERE id = ? AND user_id = ? AND revoked_at IS NULL")
      .bind(now(), deviceMatch[1], user.id)
      .run();
    return json({ ok: true });
  }

  // -- what machines share -----------------------------------------------------------------

  if (path === "/api/sync/manifest" && method === "GET") {
    const d = await device(request, env);
    const user = await owner(env, d.user_id);
    const { results } = await env.DB.prepare(
      "SELECT machine, label, os, instance, taken_at, size FROM snapshots WHERE user_id = ? ORDER BY machine",
    )
      .bind(user.id)
      .all();
    return json({ ledger: ledgerSettings(user), snapshots: results, server_time: now() });
  }

  const snapshotMatch = path.match(/^\/api\/sync\/snapshot\/([A-Za-z0-9._-]{1,64})$/);
  if (snapshotMatch && method === "GET") {
    const d = await device(request, env);
    const data = await getBlob(env.DB, d.user_id, "snapshot", snapshotMatch[1]);
    if (!data) throw new HttpError(404, "no snapshot of this machine");
    return new Response(data, { headers: { "Content-Type": "application/octet-stream", "Cache-Control": "no-store" } });
  }

  if (path === "/api/sync/snapshot" && method === "PUT") {
    const d = await device(request, env);
    const takenAt = request.headers.get("X-TokenLedger-Taken-At") ?? "";
    if (!TIME.test(takenAt)) throw new HttpError(400, "X-TokenLedger-Taken-At is missing");
    const body = await readBytes(request, MAX_SNAPSHOT);
    if (body[0] !== 0x1f || body[1] !== 0x8b) throw new HttpError(400, "a snapshot is gzip-compressed JSON");
    const held = await env.DB.prepare("SELECT taken_at FROM snapshots WHERE user_id = ? AND machine = ?")
      .bind(d.user_id, d.machine)
      .first<{ taken_at: string }>();
    if (held && Date.parse(held.taken_at) >= Date.parse(takenAt)) {
      throw new HttpError(409, "the cloud already holds a newer snapshot of this machine; is this computer's clock behind?");
    }
    await env.DB.batch([
      ...putBlob(env.DB, d.user_id, "snapshot", d.machine, body),
      env.DB.prepare(
        "INSERT INTO snapshots(user_id, machine, device_id, label, os, instance, taken_at, size, updated_at) " +
          "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(user_id, machine) DO UPDATE SET device_id = excluded.device_id, " +
          "label = excluded.label, os = excluded.os, instance = excluded.instance, taken_at = excluded.taken_at, " +
          "size = excluded.size, updated_at = excluded.updated_at",
      ).bind(d.user_id, d.machine, d.id, d.label, d.os, d.instance, takenAt, body.length, now()),
    ]);
    return json({ stored: true });
  }

  if (path === "/api/sync/dashboard" && method === "PUT") {
    const d = await device(request, env);
    const body = await readBytes(request, MAX_DASHBOARD);
    const parts = dashboardParts(body);
    const held = await env.DB.prepare("SELECT generated_at FROM dashboards WHERE user_id = ?")
      .bind(d.user_id)
      .first<{ generated_at: string }>();
    if (held && Date.parse(held.generated_at) > Date.parse(parts.generatedAt)) return json({ stored: false });
    await env.DB.batch([
      ...parts.ranges.flatMap(([name, data]) => putBlob(env.DB, d.user_id, "view", name, data)),
      env.DB.prepare(
        "INSERT INTO dashboards(user_id, generated_at, generated_by, updated_at) VALUES(?, ?, ?, ?) " +
          "ON CONFLICT(user_id) DO UPDATE SET generated_at = excluded.generated_at, generated_by = excluded.generated_by, " +
          "updated_at = excluded.updated_at",
      ).bind(d.user_id, parts.generatedAt, parts.generatedBy, now()),
    ]);
    return json({ stored: true });
  }

  if (path === "/api/sync/device" && method === "DELETE") {
    const d = await device(request, env).catch((e) => {
      if (e instanceof HttpError && e.status === 409) return null; // a recreated ledger may still sign out
      throw e;
    });
    if (d) await env.DB.prepare("UPDATE devices SET revoked_at = ? WHERE id = ?").bind(now(), d.id).run();
    return json({ ok: true });
  }

  // -- the dashboard, seen by its owner ----------------------------------------------------

  if (path === "/api/view" && method === "GET") {
    const user = await requireUser(request, env);
    const range = new URL(request.url).searchParams.get("range") ?? "all";
    if (!RANGES.includes(range)) throw new HttpError(400, `range must be one of ${RANGES.join(", ")}`);
    const data = await getBlob(env.DB, user.id, "view", range);
    if (!data) throw new HttpError(404, "no data yet: sign a machine in with `tokenledger sync login` and capture");
    const meta = await env.DB.prepare("SELECT generated_at, generated_by FROM dashboards WHERE user_id = ?")
      .bind(user.id)
      .first<{ generated_at: string; generated_by: string }>();
    return new Response(data, {
      encodeBody: "manual",
      headers: {
        "Content-Type": "application/json; charset=utf-8",
        "Content-Encoding": "gzip",
        "Cache-Control": "no-store",
        "X-TokenLedger-Generated-At": meta?.generated_at ?? "",
        "X-TokenLedger-Generated-By": encodeURIComponent(meta?.generated_by ?? ""),
      },
    });
  }

  return null;
}

async function replaced(env: Env, user: User, req: LinkRequest): Promise<{ label: string; last_seen_at: string | null } | null> {
  const active = await env.DB.prepare("SELECT * FROM devices WHERE user_id = ? AND machine = ? AND revoked_at IS NULL")
    .bind(user.id, req.machine.id)
    .first<Device & { last_seen_at: string | null }>();
  if (!active) return null;
  if (active.host !== req.host) {
    throw new HttpError(
      409,
      `another computer (${active.label}) is already signed in with this machine id: the data folder was copied. ` +
        "Start TokenLedger on this computer with an empty data folder, then sign it in",
    );
  }
  return { label: active.label, last_seen_at: active.last_seen_at };
}

// The upload format of `sync/client.py` (dashboard_container): a header line, a JSON
// index, then each range's gzip bytes. Only the small index is parsed.
function dashboardParts(body: Uint8Array): { generatedAt: string; generatedBy: string; ranges: [string, Uint8Array][] } {
  const magic = new TextEncoder().encode(DASHBOARD_MAGIC);
  if (!magic.every((b, i) => body[i] === b)) throw new HttpError(400, "not a dashboard upload");
  const end = body.indexOf(0x0a, magic.length);
  if (end < 0 || end - magic.length > 8192) throw new HttpError(400, "dashboard index missing");
  let index: { generated_at: string; generated_by: string; parts: { name: string; size: number }[] };
  try {
    index = JSON.parse(new TextDecoder().decode(body.subarray(magic.length, end)));
  } catch {
    throw new HttpError(400, "dashboard index is not JSON");
  }
  if (!TIME.test(index.generated_at ?? "")) throw new HttpError(400, "dashboard index has no time");
  let at = end + 1;
  const ranges: [string, Uint8Array][] = [];
  for (const part of index.parts ?? []) {
    if (!RANGES.includes(part.name) || !Number.isInteger(part.size) || part.size < 0) throw new HttpError(400, "bad dashboard part");
    const data = body.subarray(at, at + part.size);
    if (data.length !== part.size || data[0] !== 0x1f || data[1] !== 0x8b) throw new HttpError(400, "bad dashboard part");
    ranges.push([part.name, data]);
    at += part.size;
  }
  if (at !== body.length || ranges.length !== RANGES.length) throw new HttpError(400, "dashboard parts do not add up");
  return { generatedAt: index.generated_at, generatedBy: String(index.generated_by ?? "").slice(0, 100), ranges };
}
