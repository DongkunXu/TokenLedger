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

    pushed = after_capture(conn, paths, settings)
    if pushed is not None:  # a failed push is reported but does not fail the capture: the data stays here
        print(_push_line(pushed), file=sys.stderr)
    return 0 if all(o.ok for o in outcomes) else 1


def _push_line(result: dict) -> str:
    if not result["ok"]:
        return f"sync: not pushed: {result['error']}"
    if result.get("stale"):
        return f"sync: hub {result.get('hub')} already has a newer snapshot from this machine"
    return (
        f"sync: pushed to {result.get('hub')} ({result.get('inserted', 0)} rows added, "
        f"{result.get('updated', 0)} updated, {result.get('unchanged', 0)} unchanged)"
    )


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

    serve(host=args.host, port=args.port, open_browser=args.open, idle_hours=args.idle_hours)
    return 0


def cmd_dashboard(args) -> int:
    from . import dashboard

    try:
        port = args.port if args.port is not None else dashboard.DEFAULT_PORT
        idle = args.idle_hours if args.idle_hours is not None else dashboard.DEFAULT_IDLE_HOURS
        if args.action == "start":
            result = dashboard.start(port=port, idle_hours=idle, open_browser=args.open)
        elif args.action == "stop":
            result = dashboard.stop()
        elif args.action == "restart":
            result = dashboard.restart(port=args.port, idle_hours=args.idle_hours, open_browser=args.open)
        elif args.action == "open":
            result = dashboard.start(port=port, idle_hours=idle, open_browser=True)
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
            out = client.status(paths)
            if out["role"] == "hub":
                from .sync.service import HUB

                out["service"] = HUB.status()
            print(json.dumps(out, indent=2))
        elif args.action == "hub-init":
            cfg = client.hub_init(paths, port=args.port or sync_config.DEFAULT_PORT, host=args.host,
                                  networks=tuple(args.network or ()), key=args.key)
            print(json.dumps({"role": "hub", "key": cfg.key, "key_id": cfg.keys.key_id, "port": cfg.port,
                              "next": "run `tokenledger hub start` here, then `tokenledger sync join --key <key>` "
                                      "on every other machine"}, indent=2))
        elif args.action == "key":
            cfg = sync_config.load(paths)
            if cfg is None:
                raise client.SyncError("sync is not set up on this machine")
            print(cfg.key)
        elif args.action == "discover":
            cfg = sync_config.load(paths)
            key = args.key or (cfg.key if cfg else None)
            hubs = client.find_hubs(port=args.port or (cfg.port if cfg else sync_config.DEFAULT_PORT), key=key,
                                    networks=tuple(args.network or (cfg.networks if cfg else ())), timeout=args.timeout)
            print(json.dumps([{**h.__dict__, "url": h.url} for h in hubs], indent=2))
        elif args.action == "join":
            if not args.key:
                raise client.SyncError("pass --key with the key shown by `tokenledger sync hub-init` on the hub")
            joined = client.join(conn, paths, settings, key=args.key, hub_url=args.hub, networks=tuple(args.network or ()),
                                 port=args.port or sync_config.DEFAULT_PORT, timeout=args.timeout)
            from .config import load_settings

            pushed = client.push(conn, paths, load_settings(paths))
            print(json.dumps({**joined, "first_push": pushed}, indent=2))
        elif args.action == "push":
            result = client.push(conn, paths, settings)
            print(json.dumps(result, indent=2))
            return 0 if result["ok"] else 1
        elif args.action == "leave":
            removed = sync_config.remove(paths)
            print(json.dumps({"left": removed, "role": "standalone"}, indent=2))
    except (client.SyncError, sync_config.SyncConfigError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_hub(args) -> int:
    from .service import ServiceError
    from .sync.hub import HubNotConfigured, serve_hub
    from .sync.service import HUB, start_hub

    try:
        if args.action == "serve":
            serve_hub(host=args.host, port=args.port)
            return 0
        if args.action == "start":
            result = start_hub(host=args.host, port=args.port)
        elif args.action == "stop":
            result = HUB.stop()
        elif args.action == "restart":
            HUB.stop()
            result = start_hub(host=args.host, port=args.port)
        elif args.action == "logs":
            print(HUB.log_path().read_text() if HUB.log_path().exists() else "(no log yet)", end="")
            return 0
        else:
            result = HUB.status()
    except (ServiceError, HubNotConfigured) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
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

    p = sub.add_parser("serve", help="run the dashboard server in the foreground")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--open", action="store_true", help="open the dashboard in the browser")
    p.add_argument("--idle-hours", type=float, default=12.0, help="stop after this long without requests (0 = never)")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("dashboard", help="manage the dashboard server in the background")
    p.add_argument("action", choices=["start", "stop", "restart", "status", "open", "logs"])
    p.add_argument("--port", type=int, default=None, help="default 8765 (restart keeps the current port)")
    p.add_argument("--idle-hours", type=float, default=None, help="default 12 (restart keeps the current value)")
    p.add_argument("--open", action="store_true", help="open the dashboard in the browser")
    p.set_defaults(func=cmd_dashboard)

    p = sub.add_parser("sync", help="several machines, one ledger: set up, join, push")
    p.add_argument("action", choices=["status", "hub-init", "join", "push", "discover", "key", "leave"])
    p.add_argument("--key", help="the ledger key (join, discover; hub-init: reuse an existing key)")
    p.add_argument("--hub", help="join: the hub's fixed address, e.g. http://192.168.1.10:8766 (skips discovery)")
    p.add_argument("--network", action="append", help="CIDR to discover on / accept from (repeatable)")
    p.add_argument("--port", type=int, default=None, help="sync port (default 8766)")
    p.add_argument("--host", default="0.0.0.0", help="hub-init: address the hub listens on")
    p.add_argument("--timeout", type=float, default=2.0, help="seconds to wait for discovery answers")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("hub", help="run the sync hub (on the machine that keeps the combined ledger)")
    p.add_argument("action", choices=["serve", "start", "stop", "restart", "status", "logs"])
    p.add_argument("--host", default=None, help="address to listen on (default from sync.json, 0.0.0.0)")
    p.add_argument("--port", type=int, default=None, help="port (default from sync.json, 8766)")
    p.set_defaults(func=cmd_hub)

    p = sub.add_parser("mcp", help="run the MCP server on stdio")
    p.set_defaults(func=cmd_mcp)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
