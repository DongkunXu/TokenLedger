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
    return 0 if all(o.ok for o in outcomes) else 1


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

    p = sub.add_parser("serve", help="run the local dashboard")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--open", action="store_true", help="open the dashboard in the browser")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("mcp", help="run the MCP server on stdio")
    p.set_defaults(func=cmd_mcp)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
