"""Command-line entry point: ``tokenledger <command>``."""

from __future__ import annotations

import argparse
import json
import sys

from . import backfill, capture, sources, views
from .config import default_paths, load_settings
from .store import connect, migrate


def _open():
    paths = default_paths()
    settings = load_settings(paths)
    conn = connect(paths.db)
    migrate(conn)
    return paths, settings, conn


def cmd_capture(args) -> int:
    paths, settings, conn = _open()
    usage = sources.default_usage_sources()
    quota = sources.default_quota_sources()
    if args.source:
        wanted = set(args.source)
        usage = [s for s in usage if s.id in wanted]
        quota = [s for s in quota if s.id in wanted]
    outcomes = capture.run(conn, paths, settings, usage_sources=usage, quota_sources=quota)
    print(json.dumps([o.as_dict() for o in outcomes], indent=2))
    from .sync.client import after_capture

    synced = after_capture(conn, paths, settings)
    if synced is not None:  # a failed sync is reported but does not fail the capture: the data stays here
        print(_sync_line(synced), file=sys.stderr)
    return 0 if all(o.ok for o in outcomes) else 1


def _sync_line(result: dict) -> str:
    if not result["ok"]:
        return f"sync: failed: {result.get('error') or 'another sync is running'}"
    merged = ", ".join(m["machine"] for m in result["merged"]) or "nothing new"
    restored = " (restored this machine's settled days from the cloud first)" if result.get("restored") else ""
    return f"sync: shared this machine's data, merged from other machines: {merged}{restored}"


def cmd_backfill(args) -> int:
    paths, settings, conn = _open()
    plan = backfill.run_codexbar_legacy(conn, paths, settings, now_utc=capture.utc_now(), dry_run=args.dry_run)
    print(json.dumps(plan, indent=2))
    return 0


def cmd_accept(args) -> int:
    from .corrections import accept_revision

    paths, settings, conn = _open()
    for rid in args.revision_id:
        print(json.dumps(accept_revision(conn, paths, settings, rid, now_utc=capture.utc_now()), indent=2))
    return 0


def cmd_status(args) -> int:
    paths, settings, conn = _open()
    print(json.dumps(views.status(conn, settings), indent=2))
    return 0


def cmd_serve(args) -> int:
    from .api.server import serve

    serve(host=args.host, port=args.port, open_browser=args.open)
    return 0


def cmd_dashboard(args) -> int:
    from . import dashboard

    try:
        port = args.port if args.port is not None else dashboard.DEFAULT_PORT
        if args.action == "start":
            result = dashboard.start(port=port, open_browser=args.open)
        elif args.action == "stop":
            result = dashboard.stop()
        elif args.action == "restart":
            result = dashboard.restart(port=args.port, open_browser=args.open)
        elif args.action == "open":
            result = dashboard.start(port=port, open_browser=True)
        elif args.action == "logs":
            print(dashboard.log_path().read_text() if dashboard.log_path().exists() else "(no log yet)", end="")
            return 0
        else:
            result = dashboard.status()
    except dashboard.DashboardError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


def cmd_sync(args) -> int:
    from .sync import client
    from .sync import config as sync_config

    paths, settings, conn = _open()
    try:
        if args.action == "status":
            result = client.status(paths)
        elif args.action == "login":
            if not args.url:
                raise client.SyncError("pass the cloud ledger's address, e.g. `tokenledger sync login https://tokens.example.com`")
            result = client.login(conn, paths, settings, args.url, open_browser=not args.no_browser,
                                  say=lambda line: print(line, file=sys.stderr))
            result["sync"] = client.sync(conn, paths, load_settings(paths))
            if result["adopted"]:
                print("note: this machine took the cloud ledger's timezone; restart a running dashboard "
                      "(scripts/dashboard.sh restart)", file=sys.stderr)
        elif args.action == "now":
            result = client.sync(conn, paths, settings)
        else:
            result = client.logout(conn, paths)
    except (client.SyncError, sync_config.SyncConfigError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok", True) and result.get("sync", {}).get("ok", True) else 1


def cmd_mcp(args) -> int:
    from .mcp_server import main as mcp_main

    mcp_main()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tokenledger", description="Append-only ledger of AI token usage")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("capture", help="read all sources and update the ledger")
    p.add_argument("--source", action="append", help="limit to a source id (repeatable)")
    p.set_defaults(func=cmd_capture)

    p = sub.add_parser("backfill", help="import history from preserved CodexBar caches")
    p.add_argument("--dry-run", action="store_true", help="show what would change without writing")
    p.set_defaults(func=cmd_backfill)

    p = sub.add_parser("accept", help="adopt the source's value from a 'changed' revision")
    p.add_argument("revision_id", type=int, nargs="+")
    p.set_defaults(func=cmd_accept)

    p = sub.add_parser("status", help="show recent captures and ledger counts")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("serve", help="run the dashboard server in the foreground")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--open", action="store_true", help="open the dashboard in the browser")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("dashboard", help="manage the dashboard server in the background")
    p.add_argument("action", choices=["start", "stop", "restart", "status", "open", "logs"])
    p.add_argument("--port", type=int, default=None, help="default 8765 (restart keeps the current port)")
    p.add_argument("--open", action="store_true", help="open the dashboard in the browser")
    p.set_defaults(func=cmd_dashboard)

    p = sub.add_parser("sync", help="several machines, one ledger: sign in to a cloud ledger and sync with it")
    p.add_argument("action", choices=["status", "login", "now", "logout"])
    p.add_argument("url", nargs="?", help="login: the cloud ledger's address, e.g. https://tokens.example.com")
    p.add_argument("--no-browser", action="store_true", help="login: print the approval link without opening it")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("mcp", help="run the MCP server on stdio")
    p.set_defaults(func=cmd_mcp)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
