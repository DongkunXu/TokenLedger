"""The hub: accepts pushes from the other machines over the network.

``tokenledger hub serve`` runs this server and the discovery responder on the
sync port (TCP and UDP, 8766 by default). Unlike the dashboard it listens on
the network, so every request except ``hello`` must be sealed with the ledger
key, and requests from outside the configured networks (private ranges by
default) are refused before anything else happens.

Paths:

* ``GET /sync/v1/hello``: public; who this hub is (used for health checks)
* ``POST /sync/v1/ping``: sealed; lets a machine check the key, timezone and clocks
* ``POST /sync/v1/push``: sealed; one snapshot (``snapshot.py``)
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from starlette.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response

from .. import __version__
from ..config import Paths, default_paths, load_settings
from ..ledger import iso
from ..store import connect, migrate, repo
from . import config as sync_config
from . import envelope, snapshot
from .discovery import Responder, allowed

HELLO_PATH = "/sync/v1/hello"
PING_PATH = "/sync/v1/ping"
PUSH_PATH = "/sync/v1/push"


class HubNotConfigured(RuntimeError):
    pass


def hub_config(paths: Paths) -> sync_config.SyncConfig:
    cfg = sync_config.load(paths)
    if cfg is None or cfg.role != "hub":
        raise HubNotConfigured("this machine is not set up as a hub; run `tokenledger sync hub-init` first")
    return cfg


def create_hub_app(paths: Paths | None = None) -> FastAPI:
    paths = paths or default_paths()
    settings = load_settings(paths)
    cfg = hub_config(paths)
    keys = cfg.keys
    c = connect(paths.db)
    try:
        migrate(c)
        with repo.transaction(c):
            repo.ensure_machine(c, settings.machine, iso_now())
    finally:
        c.close()
    app = FastAPI(title="TokenLedger hub", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def only_allowed_networks(request: Request, call_next):
        client = request.client.host if request.client else ""
        if not allowed(client, cfg.networks):
            return JSONResponse({"error": "not allowed from this network"}, status_code=403)
        return await call_next(request)

    @app.get(HELLO_PATH)
    def hello():
        return {
            "app": "tokenledger",
            "protocol": envelope.PROTOCOL,
            "machine": settings.machine.id,
            "label": settings.machine.label,
            "key_id": keys.key_id,
            "version": __version__,
        }

    async def sealed(request: Request, path: str):
        body = await request.body()
        try:
            machine, request_id, payload = envelope.open_request(keys.encryption, path, request.headers, body)
        except envelope.ClockSkewError as exc:
            return None, JSONResponse({"error": str(exc), "hub_time": time.time()}, status_code=400)
        except envelope.EnvelopeError as exc:
            return None, JSONResponse({"error": str(exc)}, status_code=401)
        return (machine, request_id, payload), None

    def reply(path: str, machine: str, request_id: str, payload: dict, status: int = 200) -> Response:
        return Response(
            envelope.seal_response(keys.encryption, path, machine, request_id, payload),
            status_code=status,
            media_type=envelope.CONTENT_TYPE,
        )

    @app.post(PING_PATH)
    async def ping(request: Request):
        opened, error = await sealed(request, PING_PATH)
        if error:
            return error
        machine, request_id, _ = opened
        return reply(
            PING_PATH,
            machine,
            request_id,
            {
                "ok": True,
                "machine": settings.machine.id,
                "label": settings.machine.label,
                "timezone": settings.timezone,
                "grace_days": settings.grace_days,
                "time": time.time(),
                "version": __version__,
            },
        )

    @app.post(PUSH_PATH)
    async def push(request: Request):
        opened, error = await sealed(request, PUSH_PATH)
        if error:
            return error
        machine, request_id, payload = opened

        def merge() -> dict:
            conn = connect(paths.db, cross_thread=True)
            try:
                with repo.transaction(conn):
                    return snapshot.apply(conn, payload, sender=machine, settings=settings, now=iso_now())
            finally:
                conn.close()

        try:
            result = await run_in_threadpool(merge)
        except snapshot.SyncRefused as exc:
            return reply(PUSH_PATH, machine, request_id, {"ok": False, "error": str(exc)}, status=409)
        return reply(PUSH_PATH, machine, request_id, {"ok": True, "hub": settings.machine.id, **result})

    return app


def iso_now() -> str:
    return iso(datetime.now(timezone.utc))


def serve_hub(paths: Paths | None = None, *, host: str | None = None, port: int | None = None) -> None:
    """Run the hub (HTTP and discovery) in the foreground."""
    from ..api import server

    paths = paths or default_paths()
    settings = load_settings(paths)
    cfg = hub_config(paths)
    host = host or cfg.listen_host
    port = port or cfg.port
    app = create_hub_app(paths)
    responder = Responder(
        keys=cfg.keys,
        machine=settings.machine.id,
        label=settings.machine.label,
        port=port,
        version=__version__,
        host=host if ":" not in host else "0.0.0.0",
        networks=cfg.networks,
    ).start()
    shown = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    server.run(
        app,
        name="hub",
        host=host,
        port=port,
        url=f"http://{shown}:{port}/",
        idle_hours=0,
        on_exit=responder.stop,
        extra_state={"networks": list(cfg.networks)},
    )
