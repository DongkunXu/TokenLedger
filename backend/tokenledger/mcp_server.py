"""MCP server (stdio) so an assistant can query and maintain the ledger.

Read tools return compact JSON. ``run_capture`` and ``accept_revision`` write to
the ledger; everything else is read-only.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations

from . import capture, sources, views
from .config import default_paths, load_settings
from .ledger import local_today
from .store import connect, migrate

mcp = MCPServer(
    "tokenledger",
    instructions=(
        "TokenLedger is an append-only ledger of daily AI token usage and list-price cost "
        "(Claude, Codex, Antigravity). Days are YYYY-MM-DD in the ledger timezone. A day stays "
        "'open' for 3 days, then becomes final and never changes on its own; later disagreements "
        "from sources are listed as revisions. Codex 'account' figures come from OpenAI's servers "
        "(all devices, tokens only); 'machine' figures come from this Mac's logs via CodexBar."
    ),
)

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
WRITES = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)


@contextmanager
def _db() -> Iterator[tuple[sqlite3.Connection, object, object]]:
    paths = default_paths()
    settings = load_settings(paths)
    conn = connect(paths.db)
    try:
        migrate(conn)
        yield conn, paths, settings
    finally:
        conn.close()


def _span(conn, settings, start: str | None, end: str | None) -> tuple[str, str]:
    b = views.bounds(conn)
    today = local_today(settings, capture.utc_now()).isoformat()
    return start or (b[0] if b else today), end or today


@mcp.tool(annotations=READ_ONLY)
def ledger_status() -> dict:
    """Machine, timezone, ledger size, last successful capture per source and recent failures."""
    with _db() as (conn, _, settings):
        return views.status(conn, settings)


@mcp.tool(annotations=READ_ONLY)
def usage_summary(start: str | None = None, end: str | None = None) -> dict:
    """Totals per provider for a date range (defaults to all history): tokens, cost, cost coverage, token mix."""
    with _db() as (conn, _, settings):
        return views.summary(conn, *_span(conn, settings, start, end))


@mcp.tool(annotations=READ_ONLY)
def daily_usage(start: str | None = None, end: str | None = None, provider: str | None = None) -> dict:
    """Per-day rows: provider, tokens (best available), machine/account tokens, cost_usd, cost quality, open flag."""
    with _db() as (conn, _, settings):
        d = views.daily(conn, *_span(conn, settings, start, end))
    out = []
    for p in d["providers"]:
        if provider and p["provider"] != provider:
            continue
        for day, v in zip(d["days"], p["values"]):
            if v:
                out.append({"day": day, "provider": p["provider"], **{k: v[k] for k in v if k != "parts"}})
    return {"rows": out}


@mcp.tool(annotations=READ_ONLY)
def model_breakdown(start: str | None = None, end: str | None = None) -> dict:
    """Tokens and cost per model (this machine's logs only)."""
    with _db() as (conn, _, settings):
        return {"models": views.models(conn, *_span(conn, settings, start, end))}


@mcp.tool(annotations=READ_ONLY)
def quota_history(start: str | None = None, end: str | None = None, max_points: int = 200) -> dict:
    """Account-wide used-percent samples per provider and limit window, thinned to max_points each."""
    with _db() as (conn, _, settings):
        return {"windows": views.quota(conn, *_span(conn, settings, start, end), max_points=max_points)}


@mcp.tool(annotations=READ_ONLY)
def quota_peaks(start: str | None = None, end: str | None = None) -> dict:
    """Highest used-percent per weekly limit window (keyed by reset time) and per day for 5-hour windows."""
    with _db() as (conn, _, settings):
        return views.quota_peaks(conn, *_span(conn, settings, start, end), settings.tz)


@mcp.tool(annotations=READ_ONLY)
def quota_now() -> dict:
    """The latest used-percent of each provider's limit windows (weekly, 5-hour, ...) and when they reset."""
    with _db() as (conn, _, _settings):
        return {"windows": views.quota_now(conn)}


@mcp.tool(annotations=READ_ONLY)
def list_revisions(limit: int = 50) -> dict:
    """Where sources now disagree with final ledger rows (changed / missing / replaced), newest first."""
    with _db() as (conn, _, _s):
        return {"revisions": views.revisions(conn, limit)}


@mcp.tool(annotations=READ_ONLY)
def list_captures(limit: int = 30) -> dict:
    """Recent capture runs with status, tool version, stats and errors, newest first."""
    with _db() as (conn, _, _s):
        return {"captures": views.captures(conn, limit)}


@mcp.tool(annotations=WRITES)
def run_capture(source_ids: list[str] | None = None) -> dict:
    """Read sources now and merge into the ledger. Source ids: codexbar, codex-server, codexbar-quota."""
    with _db() as (conn, paths, settings):
        usage = sources.default_usage_sources()
        quota = sources.default_quota_sources()
        if source_ids:
            usage = [s for s in usage if s.id in source_ids]
            quota = [s for s in quota if s.id in source_ids]
        outcomes = capture.run(conn, paths, settings, usage_sources=usage, quota_sources=quota)
        return {"outcomes": [o.as_dict() for o in outcomes]}


@mcp.tool(annotations=WRITES)
def accept_revision(revision_id: int) -> dict:
    """Adopt a source's corrected value from a 'changed' revision. Writes to the ledger; the old value is kept as a revision."""
    from .corrections import accept_revision as accept

    with _db() as (conn, paths, settings):
        return accept(conn, paths, settings, revision_id, now_utc=capture.utc_now())


@mcp.tool(annotations=READ_ONLY)
def dashboard_status() -> dict:
    """Whether the dashboard web server is running: pid, URL, uptime, idle limit, log path."""
    from . import dashboard

    return dashboard.status()


@mcp.tool(annotations=WRITES)
def dashboard_start(open_browser: bool = True, idle_hours: float = 12.0) -> dict:
    """Start the dashboard server in the background (no-op if it runs). It stops by itself after idle_hours without requests (0 = never)."""
    from . import dashboard

    return dashboard.start(idle_hours=idle_hours, open_browser=open_browser)


@mcp.tool(annotations=WRITES)
def dashboard_stop() -> dict:
    """Stop the dashboard server gracefully."""
    from . import dashboard

    return dashboard.stop()


def main() -> None:
    mcp.run()
