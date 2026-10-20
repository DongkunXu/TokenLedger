"""The machine side of sync: sign in to a cloud ledger, share this machine's data,
merge the other machines', and publish the dashboard.

The cloud ledger (``cloud/``, a Cloudflare Worker) stores one snapshot per machine
and the latest dashboard; it never computes anything. Every machine does the
same in ``sync``:

1. read the list of snapshots (one per machine, with the time each was taken)
2. if the cloud holds a newer snapshot of this machine than this ledger last
   shared, its ledger is behind (restored from a backup, or recreated): settled
   days come back from it first (``snapshot.restore``)
3. merge every other machine's snapshot that is newer than the one merged last
4. share this machine's own snapshot
5. publish the dashboard: every range, computed here from the merged ledger, for
   the signed-in web dashboard

A failed sync loses nothing: every ledger keeps what it has, and the next sync
sends and fetches whole snapshots again. The outcome of every attempt is kept in
``sync-state.json`` and shown by ``tokenledger sync status`` and on the dashboard.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
import threading
import time
import urllib.error
import urllib.request
import webbrowser
import zlib
from dataclasses import replace
from datetime import datetime, timezone
from typing import Callable

from .. import __version__, build_version, views
from ..config import Paths, Settings, default_paths, load_settings, save_settings
from ..ledger import iso, local_today
from ..store import connect, migrate, repo
from . import config as sync_config
from . import snapshot

HTTP_TIMEOUT_S = 60.0
MAX_DOWNLOAD = 64 * 1024 * 1024  # a snapshot is far smaller; anything bigger is refused
SNAPSHOT_TYPE = "application/vnd.tokenledger.snapshot+gzip"
DASHBOARD_TYPE = "application/vnd.tokenledger.dashboard"
DASHBOARD_MAGIC = b"TLDASH1\n"


class SyncError(RuntimeError):
    pass


class CloudUnreachable(SyncError):
    pass


class SignedOut(SyncError):
    pass


def _now() -> str:
    return iso(datetime.now(timezone.utc))


# -- HTTP ---------------------------------------------------------------------------------


def _request(
    url: str,
    method: str,
    path: str,
    *,
    body: bytes | None = None,
    payload: dict | None = None,
    headers: dict | None = None,
    timeout: float = HTTP_TIMEOUT_S,
) -> tuple[int, dict, bytes]:
    all_headers = {"User-Agent": f"TokenLedger/{__version__}", "Accept": "application/json", **(headers or {})}
    if payload is not None:
        body = json.dumps(payload).encode()
        all_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url.rstrip("/") + path, data=body, headers=all_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), _read_capped(resp)
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()[:65536]
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as exc:
        raise CloudUnreachable(f"no answer from {url}: {getattr(exc, 'reason', exc)}") from exc


def _read_capped(resp) -> bytes:
    data = resp.read(MAX_DOWNLOAD + 1)
    if len(data) > MAX_DOWNLOAD:
        raise SyncError(f"answer larger than {MAX_DOWNLOAD // 2**20} MB refused")
    return data


def _error(status: int, data: bytes) -> str:
    try:
        return json.loads(data).get("error") or f"HTTP {status}"
    except (ValueError, AttributeError):
        return f"HTTP {status}: {data[:200]!r}"


class Session:
    """Requests made as this machine's device."""

    def __init__(self, link: sync_config.Link, conn: sqlite3.Connection) -> None:
        self.link = link
        self.headers = {
            "Authorization": f"Bearer {link.token}",
            "X-TokenLedger-Machine": link.machine,
            "X-TokenLedger-Instance": snapshot.instance(conn),
            "X-TokenLedger-Host": sync_config.host_fingerprint(),
            "X-TokenLedger-Version": build_version(),
        }

    def call(self, method: str, path: str, *, ok=(200,), **kwargs) -> tuple[int, dict, bytes]:
        status, headers, data = _request(self.link.url, method, path, headers={**self.headers, **kwargs.pop("headers", {})}, **kwargs)
        if status == 401:
            raise SignedOut(f"{_error(status, data)}; run `tokenledger sync login {self.link.url}` again")
        if status not in ok:
            raise SyncError(_error(status, data))
        return status, headers, data

    def json(self, method: str, path: str, **kwargs) -> dict:
        return json.loads(self.call(method, path, **kwargs)[2])


def _gunzip(data: bytes) -> bytes:
    d = zlib.decompressobj(16 + zlib.MAX_WBITS)
    out = d.decompress(data, MAX_DOWNLOAD)
    if d.unconsumed_tail:
        raise SyncError(f"snapshot larger than {MAX_DOWNLOAD // 2**20} MB uncompressed refused")
    return out


# -- signing in ---------------------------------------------------------------------------


def login(
    conn: sqlite3.Connection,
    paths: Paths,
    settings: Settings,
    url: str,
    *,
    open_browser: bool = True,
    say: Callable[[str], None] = print,
) -> dict:
    """Sign this machine in to the cloud ledger at ``url``: the person approves it in the
    browser, then this machine receives its own device token."""
    url = url.rstrip("/")
    if not url.startswith("http"):
        url = "https://" + url
    me = settings.machine
    status, _, data = _request(
        url,
        "POST",
        "/api/link/start",
        payload={
            "machine": {"id": me.id, "label": me.label, "os": me.os},
            "instance": snapshot.instance(conn),
            "host": sync_config.host_fingerprint(),
            "app_version": build_version(),
            "timezone": settings.timezone,
            "grace_days": settings.grace_days,
        },
    )
    if status != 200:
        raise SyncError(f"{url} refused to start signing in: {_error(status, data)}")
    start = json.loads(data)
    say(f"Approve this computer in the browser: {start['verify_url']}")
    say(f"Check that the page shows the code {start['user_code']}.")
    if open_browser:
        webbrowser.open(start["verify_url"])

    deadline = time.monotonic() + float(start["expires_in"])
    while True:
        if time.monotonic() > deadline:
            raise SyncError("not approved in time; run `tokenledger sync login` again")
        time.sleep(float(start.get("interval", 3)))
        status, _, data = _request(url, "POST", "/api/link/poll", payload={"device_code": start["device_code"]})
        if status == 404:
            raise SyncError("the sign-in request expired; run `tokenledger sync login` again")
        if status != 200:
            raise SyncError(_error(status, data))
        answer = json.loads(data)
        if answer["status"] == "pending":
            continue
        if answer["status"] != "approved":
            raise SyncError("signing in was declined in the browser")
        break

    link = sync_config.Link(
        url=url, user=answer["user"], device_id=answer["device_id"], token=answer["token"], machine=me.id, linked_at=_now()
    )
    ledger = answer["ledger"]
    adopted = _adopt_ledger_settings(conn, paths, settings, ledger, link)
    sync_config.save(paths, link)
    return {"url": url, "user": link.user, "device_id": link.device_id, "adopted": adopted}


def _adopt_ledger_settings(
    conn: sqlite3.Connection, paths: Paths, settings: Settings, ledger: dict, link: sync_config.Link
) -> dict:
    """Days must line up on every machine: a machine with an empty ledger takes the cloud
    ledger's timezone and grace period; one with data must already have them."""
    wanted = {"timezone": ledger["timezone"], "grace_days": int(ledger["grace_days"])}
    have = {"timezone": settings.timezone, "grace_days": settings.grace_days}
    if wanted == have:
        return {}
    if conn.execute("SELECT 1 FROM ledger LIMIT 1").fetchone() is None:
        save_settings(paths, replace(settings, **wanted))
        return wanted
    try:
        Session(link, conn).call("DELETE", "/api/sync/device")
    except SyncError:
        pass
    raise SyncError(
        f"the cloud ledger uses timezone {wanted['timezone']} and grace_days {wanted['grace_days']}, this machine "
        f"{have['timezone']} and {have['grace_days']}, and this ledger already has days in it. Set the same values in "
        "data/settings.json, then sign in again"
    )


def logout(conn: sqlite3.Connection, paths: Paths) -> dict:
    """Sign this machine out (its device token stops working) and forget the cloud ledger.
    The local ledger, with every machine's data merged so far, stays."""
    link = sync_config.load(paths)
    if link is None:
        return {"signed_out": False, "was_signed_in": False}
    revoked = True
    try:
        Session(link, conn).call("DELETE", "/api/sync/device")
    except SignedOut:
        pass  # already revoked
    except SyncError:
        revoked = False
    sync_config.remove(paths)
    return {"signed_out": True, "was_signed_in": True, "revoked_in_cloud": revoked}


# -- syncing ------------------------------------------------------------------------------


def sync(conn: sqlite3.Connection, paths: Paths, settings: Settings, *, wait: bool = True) -> dict:
    """One full sync (see the module docstring) under this ledger's sync lock; records the
    outcome. Returns ``{"ok": False, "busy": True}`` without waiting when ``wait`` is off and
    another process is syncing."""
    link = sync_config.load(paths)
    if link is None:
        raise SyncError("this machine is not signed in to a cloud ledger; run `tokenledger sync login <url>`")
    with sync_config.exclusive(paths, wait=wait) as held:
        if not held:
            return {"ok": False, "busy": True}
        state = sync_config.load_state(paths)
        state["last_attempt_at"] = _now()
        try:
            result = _sync(conn, settings, link, state)
        except SyncError as exc:
            state.update(last_ok=False, last_error=str(exc), signed_out=isinstance(exc, SignedOut))
            sync_config.save_state(paths, state)
            return {"ok": False, "error": str(exc)}
        state.update(last_ok=True, last_error=None, signed_out=False, last_ok_at=state["last_attempt_at"], last_stats=result)
        sync_config.save_state(paths, state)
        return {"ok": True, **result}


def _sync(conn: sqlite3.Connection, settings: Settings, link: sync_config.Link, state: dict) -> dict:
    session = Session(link, conn)
    manifest = session.json("GET", "/api/sync/manifest")
    ledger = manifest["ledger"]
    if (ledger["timezone"], int(ledger["grace_days"])) != (settings.timezone, settings.grace_days):
        raise SyncError(
            f"the cloud ledger uses timezone {ledger['timezone']} and grace_days {ledger['grace_days']}, this machine "
            f"{settings.timezone} and {settings.grace_days}; set the same values in data/settings.json"
        )
    me = settings.machine.id
    now = _now()
    out: dict = {"restored": None, "merged": [], "skipped": []}

    shared_at = repo.meta_get(conn, "shared_at") or ""
    for entry in manifest["snapshots"]:
        if entry["machine"] == me and entry["taken_at"] > shared_at:
            data = json.loads(_gunzip(session.call("GET", f"/api/sync/snapshot/{entry['machine']}")[2]))
            with repo.transaction(conn):
                out["restored"] = snapshot.restore(conn, data, settings=settings, now=now)
                repo.meta_set(conn, "shared_at", data["taken_at"])

    held = {r["id"]: r["snapshot_at"] or "" for r in conn.execute("SELECT id, snapshot_at FROM machines")}
    for entry in manifest["snapshots"]:
        machine = entry["machine"]
        if machine == me or entry["taken_at"] <= held.get(machine, ""):
            continue
        data = json.loads(_gunzip(session.call("GET", f"/api/sync/snapshot/{machine}")[2]))
        try:
            with repo.transaction(conn):
                stats = snapshot.apply(conn, data, sender=machine, settings=settings, now=now)
        except snapshot.SyncRefused as exc:
            out["skipped"].append({"machine": entry.get("label") or machine, "error": str(exc)})
            continue
        out["merged"].append({"machine": entry.get("label") or machine, **stats})

    taken_at = snapshot.snapshot_time()
    body = gzip.compress(json.dumps(snapshot.build(conn, settings, app_version=build_version(), taken_at=taken_at)).encode())
    session.call(
        "PUT", "/api/sync/snapshot", body=body, headers={"Content-Type": SNAPSHOT_TYPE, "X-TokenLedger-Taken-At": taken_at}
    )
    with repo.transaction(conn):
        repo.meta_set(conn, "shared_at", taken_at)
    out["shared"] = {"taken_at": taken_at, "bytes": len(body)}

    # Uploaded only after the rest of this sync went through, so it reports this sync as done.
    synced = {"signed_in": True, "url": link.url, "user": link.user, "last_attempt_at": now, "last_ok": True,
              "last_ok_at": now, "last_error": None, "signed_out": False}
    container, digest = dashboard_container(conn, settings, synced)
    if digest != state.get("dashboard_digest"):
        session.call("PUT", "/api/sync/dashboard", body=container, headers={"Content-Type": DASHBOARD_TYPE})
        state["dashboard_digest"] = digest
        out["dashboard"] = {"bytes": len(container)}
    if out["skipped"]:
        raise SyncError("; ".join(f"{s['machine']}: {s['error']}" for s in out["skipped"]))
    return out


def dashboard_container(conn: sqlite3.Connection, settings: Settings, synced: dict) -> tuple[bytes, str]:
    """Every dashboard range of the whole ledger (every machine), each compressed on its own
    so the cloud can hand one out as it is: a header line, a JSON index, then the parts. The
    digest leaves the times of this sync out, so an unchanged dashboard is not uploaded again."""
    today = local_today(settings, datetime.now(timezone.utc)).isoformat()
    parts = []
    digest = hashlib.sha256()
    for key in views.RANGE_DAYS:
        view = views.dashboard(conn, settings, key, today, scope="ledger", version=build_version(), sync={})
        digest.update(json.dumps(view, separators=(",", ":")).encode())
        view["status"]["sync"] = synced
        parts.append((key, gzip.compress(json.dumps(view, separators=(",", ":")).encode(), mtime=0)))
    index = {
        "generated_at": _now(),
        "generated_by": settings.machine.label,
        "parts": [{"name": name, "size": len(data)} for name, data in parts],
    }
    container = DASHBOARD_MAGIC + json.dumps(index).encode() + b"\n" + b"".join(data for _, data in parts)
    return container, digest.hexdigest()


def after_capture(conn: sqlite3.Connection, paths: Paths, settings: Settings) -> dict | None:
    """Sync after a capture when this machine is signed in; None otherwise."""
    try:
        if sync_config.load(paths) is None:
            return None
        return sync(conn, paths, settings)
    except Exception as exc:  # noqa: BLE001 - a failed sync must never fail the capture
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def status(paths: Paths) -> dict:
    """Whether this machine is signed in, and the outcome of its last sync."""
    try:
        link = sync_config.load(paths)
    except sync_config.SyncConfigError as exc:
        return {"signed_in": False, "error": str(exc)}
    if link is None:
        return {"signed_in": False}
    state = sync_config.load_state(paths)
    return {
        "signed_in": True,
        "url": link.url,
        "user": link.user,
        "device_id": link.device_id,
        "linked_at": link.linked_at,
        "last_attempt_at": state.get("last_attempt_at"),
        "last_ok": state.get("last_ok"),
        "last_ok_at": state.get("last_ok_at"),
        "last_error": state.get("last_error"),
        "signed_out": bool(state.get("signed_out")),
    }
