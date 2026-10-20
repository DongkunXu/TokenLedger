"""Deploy and manage a TokenLedger cloud ledger with wrangler. Run through scripts/cloud.sh.

Everything specific to one deployment (the domain, the D1 database id) lives in
data/cloud.json, outside git. The wrangler configuration is generated from it into
.build/cloud/ for each command.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLOUD = ROOT / "cloud"
CONFIG = ROOT / "data" / "cloud.json"
BUILD = ROOT / ".build" / "cloud"
COMPATIBILITY_DATE = "2026-10-01"
CODE_HOURS = 24
LOGIN_SCOPES = (
    "account:read", "user:read", "workers:write", "workers_scripts:write", "workers_routes:write",
    "d1:write", "zone:read", "ssl_certs:write",
)
DOMAIN = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def wrangler(*args: str, capture: bool = False, check: bool = True) -> subprocess.CompletedProcess:
    command = ["npx", "--no-install", "wrangler", *args]
    out = subprocess.run(command, cwd=CLOUD, text=True, capture_output=capture)
    if check and out.returncode != 0:
        detail = (out.stderr or out.stdout or "").strip() if capture else ""
        sys.exit(f"wrangler {' '.join(args[:2])} failed{': ' + detail[-2000:] if detail else ''}")
    return out


def load() -> dict:
    return json.loads(CONFIG.read_text()) if CONFIG.exists() else {}


def save(conf: dict) -> None:
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(conf, indent=2) + "\n")


def write_config(conf: dict, *, dev: bool = False, assets: Path | None = None, path: Path | None = None) -> Path:
    """The wrangler configuration for this deployment (or for local development)."""
    path = path or BUILD / ("wrangler.dev.toml" if dev else "wrangler.toml")
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f'name = "{conf["worker"]}"',
        f'main = "{CLOUD / "src" / "index.ts"}"',
        f'compatibility_date = "{COMPATIBILITY_DATE}"',
        "workers_dev = false",
        "preview_urls = false",
        "",
        "[assets]",
        f'directory = "{assets or ROOT / "frontend" / "dist"}"',
        'binding = "ASSETS"',
        'not_found_handling = "single-page-application"',
        "run_worker_first = true",
        "",
        "[[d1_databases]]",
        'binding = "DB"',
        f'database_name = "{conf["database"]}"',
        f'database_id = "{conf.get("database_id") or "00000000-0000-0000-0000-000000000000"}"',
        f'migrations_dir = "{CLOUD / "migrations"}"',
    ]
    if not dev:
        lines += ["", "[[routes]]", f'pattern = "{conf["domain"]}"', "custom_domain = true"]
    path.write_text("\n".join(lines) + "\n")
    return path


def logged_in() -> bool:
    out = wrangler("whoami", capture=True, check=False)
    return out.returncode == 0 and "You are not authenticated" not in (out.stdout + out.stderr)


def build_frontend() -> None:
    subprocess.run([str(ROOT / "scripts" / "build.sh")], check=True)


def database_id(name: str) -> str | None:
    out = wrangler("d1", "list", "--json", capture=True)
    for db in json.loads(out.stdout or "[]"):
        if db.get("name") == name:
            return db.get("uuid")
    return None


def d1(conf: dict, config: Path, sql: str, *, remote: bool = True) -> list[dict]:
    where = "--remote" if remote else "--local"
    extra = [] if remote else ["--persist-to", str(BUILD / "state")]
    out = wrangler("d1", "execute", conf["database"], where, *extra, "--config", str(config), "--json", "--command", sql, capture=True)
    data = json.loads(out.stdout)
    return data[0].get("results", []) if data else []


def new_code(conf: dict, config: Path, kind: str, user_id: str | None, *, remote: bool = True) -> str:
    code = secrets.token_urlsafe(24)
    digest = hashlib.sha256(code.encode()).hexdigest()
    now = datetime.now(timezone.utc)
    expires = (now + timedelta(hours=CODE_HOURS)).isoformat().replace("+00:00", "Z")
    user = f"'{user_id}'" if user_id else "NULL"
    d1(conf, config, f"INSERT INTO codes(id, kind, user_id, created_at, expires_at) VALUES('{digest}', '{kind}', {user}, "
       f"'{now.isoformat().replace('+00:00', 'Z')}', '{expires}')", remote=remote)
    return code


def setup_link(conf: dict, config: Path, *, remote: bool = True, base: str | None = None) -> str | None:
    if d1(conf, config, "SELECT 1 AS x FROM users LIMIT 1", remote=remote):
        return None
    code = new_code(conf, config, "setup", None, remote=remote)
    return f"{base or 'https://' + conf['domain']}/setup#{code}"


def cmd_login(*extra: str) -> None:
    # Only what deploying needs: the Worker and its custom domain, the D1 database.
    wrangler("login", "--scopes", *LOGIN_SCOPES, *extra)


def cmd_deploy(domain: str | None) -> None:
    conf = load()
    if domain:
        domain = domain.lower().strip().removeprefix("https://").rstrip("/")
        if not DOMAIN.match(domain):
            sys.exit(f"not a domain name: {domain!r}")
        if conf.get("domain") and conf["domain"] != domain:
            sys.exit(f"data/cloud.json already deploys to {conf['domain']}; edit it to move")
        conf = {"worker": "tokenledger", "database": "tokenledger", **conf, "domain": domain}
    if not conf.get("domain"):
        sys.exit("first deploy: scripts/cloud.sh deploy <domain>, e.g. tokens.example.com")
    if not logged_in():
        sys.exit("not signed in to Cloudflare: run scripts/cloud.sh login first")
    if not conf.get("database_id"):
        conf["database_id"] = database_id(conf["database"])
        if not conf["database_id"]:
            wrangler("d1", "create", conf["database"])
            conf["database_id"] = database_id(conf["database"])
        if not conf["database_id"]:
            sys.exit("could not create the D1 database")
    save(conf)
    config = write_config(conf)
    build_frontend()
    os.environ["CI"] = "true"  # apply migrations without asking
    wrangler("d1", "migrations", "apply", conf["database"], "--remote", "--config", str(config))
    wrangler("deploy", "--config", str(config))
    print(f"\ndeployed: https://{conf['domain']}")
    link = setup_link(conf, config)
    if link:
        print(f"create your account (valid {CODE_HOURS} h, once): {link}")


def cmd_setup_code() -> None:
    conf = _need()
    link = setup_link(conf, write_config(conf))
    print(link or "the ledger already has an account; invite others from the dashboard's account menu")


def cmd_recover(name: str) -> None:
    conf = _need()
    config = write_config(conf)
    safe = name.replace("'", "''")
    users = d1(conf, config, f"SELECT id FROM users WHERE name = '{safe}' COLLATE NOCASE")
    if not users:
        sys.exit(f"no account named {name!r}")
    code = new_code(conf, config, "recover", users[0]["id"])
    print(f"add a passkey to {name} (valid {CODE_HOURS} h, once): https://{conf['domain']}/recover#{code}")


def cmd_status() -> None:
    conf = load()
    print(json.dumps({"config": str(CONFIG), **conf, "signed_in_to_cloudflare": logged_in()}, indent=2))


def cmd_dev() -> None:
    conf = {"worker": "tokenledger-dev", "database": "tokenledger-dev", "domain": "127.0.0.1"}
    config = write_config(conf, dev=True)
    build_frontend()
    persist = ["--persist-to", str(BUILD / "state")]
    os.environ["CI"] = "true"
    wrangler("d1", "migrations", "apply", conf["database"], "--local", *persist, "--config", str(config))
    link = setup_link(conf, config, remote=False, base="http://127.0.0.1:8787")
    if link:
        print(f"create the first local account: {link}")
    wrangler("dev", "--local", "--ip", "127.0.0.1", "--port", "8787", *persist, "--config", str(config))


def _need() -> dict:
    conf = load()
    if not conf.get("database_id"):
        sys.exit("nothing deployed yet: scripts/cloud.sh deploy <domain>")
    return conf


def main(argv: list[str]) -> None:
    sys.stdout.reconfigure(line_buffering=True)  # links show up before wrangler takes over
    if not argv:
        sys.exit(__doc__)
    command, *rest = argv
    if command == "login":
        cmd_login(*rest)
    elif command == "deploy":
        cmd_deploy(rest[0] if rest else None)
    elif command == "setup-code":
        cmd_setup_code()
    elif command == "recover" and rest:
        cmd_recover(rest[0])
    elif command == "status":
        cmd_status()
    elif command == "dev":
        cmd_dev()
    else:
        sys.exit("usage: scripts/cloud.sh login | deploy [domain] | setup-code | recover <name> | status | dev")


if __name__ == "__main__":
    main(sys.argv[1:])
