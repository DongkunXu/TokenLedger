"""The machine side of sync: set up a hub, join one, push to it.

A push sends this machine's snapshot (``snapshot.py``) to the hub. The hub is
looked up in this order: the fixed ``hub_url`` if one is set (no discovery);
otherwise the address where it answered last time, then a discovery broadcast
that accepts only the hub with this ledger's key and the hub's machine id. The
outcome of every attempt is kept in ``sync-state.json`` and shown by
``tokenledger sync status`` and on the dashboard.

A failed push loses nothing: the local ledger keeps everything, and the next
push sends the whole snapshot again.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
import urllib.error
import urllib.request
from datetime import datetime, timezone

from .. import __version__
from ..config import Paths, Settings, save_settings
from ..ledger import iso
from . import config as sync_config
from . import envelope, snapshot
from .discovery import HubInfo, discover
from .hub import HELLO_PATH, PING_PATH, PUSH_PATH

HTTP_TIMEOUT_S = 30.0
CONNECT_ERRORS = (urllib.error.URLError, ConnectionError, TimeoutError, OSError)


class SyncError(RuntimeError):
    pass


class HubUnreachable(SyncError):
    pass


def _now() -> str:
    return iso(datetime.now(timezone.utc))


def call(url: str, path: str, keys: sync_config.Keys, machine: str, payload: dict, *, timeout: float = HTTP_TIMEOUT_S) -> dict:
    """One sealed request; returns the hub's sealed answer. Raises HubUnreachable when
    nothing answered and SyncError when the hub answered with an error."""
    headers, body = envelope.new_request(keys.encryption, path, machine, payload)
    request = urllib.request.Request(url.rstrip("/") + path, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            status, ctype, data = resp.status, resp.headers.get("Content-Type", ""), resp.read()
    except urllib.error.HTTPError as exc:
        status, ctype, data = exc.code, exc.headers.get("Content-Type", ""), exc.read()
    except CONNECT_ERRORS as exc:
        raise HubUnreachable(f"no answer from {url}: {getattr(exc, 'reason', exc)}") from exc
    if ctype.startswith(envelope.CONTENT_TYPE):
        try:
            answer = envelope.open_response(keys.encryption, path, machine, headers[envelope.H_REQUEST], data)
        except envelope.EnvelopeError as exc:
            raise SyncError(f"{url} answered with a message this key cannot open: {exc}") from exc
        if status != 200 or not answer.get("ok"):
            raise SyncError(f"hub refused: {answer.get('error') or status}")
        return answer
    try:
        message = json.loads(data).get("error")
    except (ValueError, AttributeError):
        message = None
    if status == 401:
        raise SyncError("the hub does not accept this key (was the key changed or mistyped?)")
    raise SyncError(f"{url} answered {status}: {message or data[:200]!r}")


def hello(url: str, *, timeout: float = 5.0) -> dict:
    try:
        with urllib.request.urlopen(url.rstrip("/") + HELLO_PATH, timeout=timeout) as resp:
            return json.loads(resp.read())
    except CONNECT_ERRORS as exc:
        raise HubUnreachable(f"no answer from {url}: {getattr(exc, 'reason', exc)}") from exc


# -- setting up ---------------------------------------------------------------------------


def hub_init(
    paths: Paths, *, port: int = sync_config.DEFAULT_PORT, host: str = "0.0.0.0", networks: tuple[str, ...] = (), key: str | None = None
) -> sync_config.SyncConfig:
    """Make this machine the hub of a new (or, with ``key``, an existing) ledger group."""
    current = sync_config.load(paths)
    if current and current.role == "node":
        raise SyncError("this machine is set up as a node; run `tokenledger sync leave` first")
    cfg = sync_config.SyncConfig(
        role="hub",
        key=key or (current.key if current else sync_config.new_key()),
        port=port,
        listen_host=host,
        networks=tuple(networks),
    )
    sync_config.save(paths, cfg)
    return cfg


def find_hubs(*, port: int, key: str | None, networks: tuple[str, ...] = (), timeout: float = 2.0) -> list[HubInfo]:
    keys = sync_config.Keys.derive(key) if key else None
    return discover(port=port, keys=keys, networks=networks, timeout=timeout)


def join(
    conn: sqlite3.Connection,
    paths: Paths,
    settings: Settings,
    *,
    key: str,
    hub_url: str | None = None,
    networks: tuple[str, ...] = (),
    port: int = sync_config.DEFAULT_PORT,
    timeout: float = 2.0,
) -> dict:
    """Join the ledger group whose hub holds ``key``, found on the network or at ``hub_url``."""
    current = sync_config.load(paths)
    if current and current.role == "hub":
        raise SyncError("this machine is the hub; run `tokenledger sync leave` first to make it a node")
    keys = sync_config.Keys.derive(key)
    if hub_url:
        url, fixed = hub_url.rstrip("/"), True
    else:
        hubs = discover(port=port, keys=keys, networks=networks, timeout=timeout)
        if not hubs:
            raise SyncError(
                "no hub with this key answered on the local network. Is `tokenledger hub start` running there, "
                "and are both machines on the same network? Otherwise pass --hub http://<address>:8766"
            )
        if len({h.machine for h in hubs}) > 1:
            listed = ", ".join(f"{h.label} ({h.url})" for h in hubs)
            raise SyncError(f"several hubs hold this key: {listed}; pass --hub with the one to use")
        url, fixed = hubs[0].url, False
    info = call(url, PING_PATH, keys, settings.machine.id, {"hello": True})
    if info["machine"] == settings.machine.id:
        raise SyncError("the hub has this machine's id; this data folder seems to be a copy of the hub's")

    adopted_timezone = False
    if info["timezone"] != settings.timezone:
        has_rows = conn.execute("SELECT 1 FROM ledger LIMIT 1").fetchone() is not None
        if has_rows:
            raise SyncError(
                f"the hub buckets days in {info['timezone']} and this machine in {settings.timezone}, and this "
                "ledger already has days in it. Set the same timezone in data/settings.json on both machines"
            )
        save_settings(paths, replace(settings, timezone=info["timezone"]))
        adopted_timezone = True

    cfg = sync_config.SyncConfig(
        role="node",
        key=key,
        port=port,
        networks=tuple(networks),
        hub_url=url if fixed else None,
        hub_machine=info["machine"],
        hub_label=info["label"],
        hub_last_url=url,
    )
    sync_config.save(paths, cfg)
    return {"hub": info["label"], "hub_machine": info["machine"], "url": url, "fixed_url": fixed, "adopted_timezone": adopted_timezone}


# -- pushing ------------------------------------------------------------------------------


def push(conn: sqlite3.Connection, paths: Paths, settings: Settings, *, discover_timeout: float = 2.0) -> dict:
    """Send this machine's snapshot to its hub and record the outcome."""
    cfg = sync_config.load(paths)
    if cfg is None or cfg.role != "node":
        raise SyncError("this machine is not set up as a node; run `tokenledger sync join` first")
    state = sync_config.load_state(paths)
    state["last_attempt_at"] = _now()
    try:
        result, url = _push(conn, settings, cfg, discover_timeout=discover_timeout)
    except SyncError as exc:
        state.update(last_ok=False, last_error=str(exc))
        sync_config.save_state(paths, state)
        return {"ok": False, "error": str(exc)}
    if not cfg.hub_url and url != cfg.hub_last_url:
        sync_config.save(paths, cfg.with_(hub_last_url=url))
    stats = {k: v for k, v in result.items() if k not in ("ok", "hub")}
    state.update(last_ok=True, last_error=None, last_ok_at=state["last_attempt_at"], last_url=url, last_stats=stats)
    sync_config.save_state(paths, state)
    return {"ok": True, "hub": cfg.hub_label, "url": url, **stats}


def _push(conn: sqlite3.Connection, settings: Settings, cfg: sync_config.SyncConfig, *, discover_timeout: float) -> tuple[dict, str]:
    keys = cfg.keys
    payload = snapshot.build(conn, settings, app_version=__version__)
    tried: list[str] = []

    def attempt(url: str) -> dict:
        tried.append(url)
        answer = call(url, PUSH_PATH, keys, settings.machine.id, payload)
        if cfg.hub_machine and answer.get("hub") != cfg.hub_machine:
            raise SyncError(f"{url} is a different hub ({answer.get('hub')}) than the one this machine joined")
        return answer

    if cfg.hub_url:
        return attempt(cfg.hub_url), cfg.hub_url
    if cfg.hub_last_url:
        try:
            return attempt(cfg.hub_last_url), cfg.hub_last_url
        except HubUnreachable:
            pass  # the hub may have a new address; look for it
    for hub in discover(port=cfg.port, keys=keys, networks=cfg.networks, timeout=discover_timeout):
        if cfg.hub_machine and hub.machine != cfg.hub_machine:
            continue
        if hub.url in tried:
            continue
        try:
            return attempt(hub.url), hub.url
        except HubUnreachable:
            continue
    where = f" (tried {', '.join(tried)})" if tried else ""
    raise HubUnreachable(f"hub {cfg.hub_label or ''} not reachable{where}; data stays here and goes with the next push")


def after_capture(conn: sqlite3.Connection, paths: Paths, settings: Settings) -> dict | None:
    """Push after a capture when this machine is a node; None otherwise."""
    cfg = sync_config.load(paths)
    if cfg is None or cfg.role != "node":
        return None
    try:
        return push(conn, paths, settings)
    except Exception as exc:  # noqa: BLE001 - a failed push must never fail the capture
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def status(paths: Paths) -> dict:
    """Sync role and the outcome of the last push, for the CLI and the dashboard."""
    cfg = sync_config.load(paths)
    if cfg is None:
        return {"role": "standalone"}
    out: dict = {"role": cfg.role, "key_id": cfg.keys.key_id, "port": cfg.port, "networks": list(cfg.networks)}
    if cfg.role == "node":
        state = sync_config.load_state(paths)
        out.update(
            hub=cfg.hub_label,
            hub_machine=cfg.hub_machine,
            hub_url=cfg.hub_url or cfg.hub_last_url,
            fixed_url=bool(cfg.hub_url),
            last_attempt_at=state.get("last_attempt_at"),
            last_ok=state.get("last_ok"),
            last_ok_at=state.get("last_ok_at"),
            last_error=state.get("last_error"),
        )
    else:
        out.update(listen=f"{cfg.listen_host}:{cfg.port}")
    return out
