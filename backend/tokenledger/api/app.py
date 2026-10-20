"""HTTP API for the dashboard. Read-only except ``POST /api/capture``.

Every request opens its own SQLite connection. Dates are ``YYYY-MM-DD``; when
``start``/``end`` are omitted the whole ledger range is used.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from typing import Iterator

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__, build_version, capture, views
from ..config import Paths, Settings, default_paths, load_settings, project_root
from ..ledger import local_today
from ..store import connect, migrate
from ..sync import client as sync_client

def create_app(paths: Paths | None = None, static_dir: Path | None = None) -> FastAPI:
    paths = paths or default_paths()
    settings = load_settings(paths)
    c = connect(paths.db)
    try:
        migrate(c)
    finally:
        c.close()
    app = FastAPI(title="TokenLedger", version=__version__, docs_url="/api/docs", openapi_url="/api/openapi.json")

    def db() -> Iterator[sqlite3.Connection]:
        conn = connect(paths.db, cross_thread=True)
        try:
            yield conn
        finally:
            conn.close()

    def get_settings() -> Settings:
        return settings

    def span(conn: sqlite3.Connection, start: str | None, end: str | None) -> tuple[str, str]:
        b = views.bounds(conn)
        today = local_today(settings, capture.utc_now()).isoformat()
        lo = start or (b[0] if b else today)
        hi = end or today
        for label, value in (("start", lo), ("end", hi)):
            try:
                date.fromisoformat(value)
            except ValueError as exc:
                raise HTTPException(400, f"{label} must be YYYY-MM-DD") from exc
        if lo > hi:
            raise HTTPException(400, "start is after end")
        return lo, hi

    @app.get("/api/session")
    def session():
        """Which dashboard this is; the cloud dashboard answers with the signed-in person."""
        return {"mode": "local"}

    @app.get("/api/status")
    def status(conn=Depends(db), s: Settings = Depends(get_settings)):
        return {
            **views.status(conn, s),
            "version": build_version(),
            "today": local_today(s, capture.utc_now()).isoformat(),
            "sync": sync_client.status(paths),
        }

    @app.get("/api/view")
    def view(range_key: str = Query("all", alias="range"), conn=Depends(db), s: Settings = Depends(get_settings)):
        """Everything one dashboard range shows (the cloud dashboard serves the same shape)."""
        if range_key not in views.RANGE_DAYS:
            raise HTTPException(400, f"range must be one of {', '.join(views.RANGE_DAYS)}")
        # This machine's own data; the cloud dashboard shows every machine (sync/client.py).
        today = local_today(s, capture.utc_now()).isoformat()
        return views.dashboard(conn, s, range_key, today, scope="machine", version=build_version(), sync=sync_client.status(paths))

    @app.get("/api/summary")
    def summary(start: str | None = None, end: str | None = None, conn=Depends(db)):
        return views.summary(conn, *span(conn, start, end))

    @app.get("/api/daily")
    def daily(start: str | None = None, end: str | None = None, conn=Depends(db)):
        return views.daily(conn, *span(conn, start, end))

    @app.get("/api/models")
    def models(start: str | None = None, end: str | None = None, conn=Depends(db)):
        return views.models(conn, *span(conn, start, end))

    @app.get("/api/quota")
    def quota(
        start: str | None = None, end: str | None = None, max_points: int = Query(600, ge=10, le=5000), conn=Depends(db)
    ):
        return views.quota(conn, *span(conn, start, end), max_points=max_points)

    @app.get("/api/quota/peaks")
    def quota_peaks(start: str | None = None, end: str | None = None, conn=Depends(db)):
        return views.quota_peaks(conn, *span(conn, start, end), settings.tz)

    @app.get("/api/quota/now")
    def quota_now(conn=Depends(db)):
        return views.quota_now(conn)

    @app.get("/api/revisions")
    def revisions(limit: int = Query(100, ge=1, le=1000), conn=Depends(db)):
        return views.revisions(conn, limit)

    @app.get("/api/captures")
    def captures(limit: int = Query(50, ge=1, le=500), conn=Depends(db)):
        return views.captures(conn, limit)

    @app.post("/api/capture")
    def run_capture(conn=Depends(db)):
        outcomes = capture.run_default(conn, paths, settings)
        if outcomes is None:
            raise HTTPException(409, "a capture is already running")
        return [o.as_dict() for o in outcomes]

    static = static_dir or project_root() / "frontend" / "dist"
    if static.is_dir():
        app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                raise HTTPException(404)
            candidate = (static / path).resolve()
            if path and candidate.is_file() and static.resolve() in candidate.parents:
                return FileResponse(candidate)
            # The page names its hashed assets, so it must be revalidated after every build;
            # the assets themselves can be cached as long as the browser likes.
            return FileResponse(static / "index.html", headers={"Cache-Control": "no-cache"})

    return app
