"""The cloud ledger end to end: the real Worker (cloud/) in wrangler's local mode, two
machines syncing through it, and people signing in with passkeys (a software
authenticator stands in for Touch ID).

Slow and needs Node with cloud/node_modules, so it runs only with
TOKENLEDGER_CLOUD_TEST=1 (``scripts/test.sh --cloud``).
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("TOKENLEDGER_CLOUD_TEST") != "1", reason="set TOKENLEDGER_CLOUD_TEST=1")

from cryptography.hazmat.primitives import hashes  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa  # noqa: E402

from tokenledger.config import Machine, Paths, Settings, load_settings, save_settings  # noqa: E402
from tokenledger.models import Scope, SeriesKey  # noqa: E402
from tokenledger.store import connect, migrate  # noqa: E402
from tokenledger.sync import client  # noqa: E402
from tokenledger.sync import config as sync_config  # noqa: E402

from test_sync import capture_into, usage  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import cloud as cloud_tool  # noqa: E402


# -- a software passkey -------------------------------------------------------------------


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def cbor(value) -> bytes:
    def head(major: int, n: int) -> bytes:
        if n < 24:
            return bytes([major << 5 | n])
        if n < 256:
            return bytes([major << 5 | 24, n])
        if n < 65536:
            return bytes([major << 5 | 25]) + struct.pack(">H", n)
        return bytes([major << 5 | 26]) + struct.pack(">I", n)

    if isinstance(value, int):
        return head(0, value) if value >= 0 else head(1, -1 - value)
    if isinstance(value, bytes):
        return head(2, len(value)) + value
    if isinstance(value, str):
        raw = value.encode()
        return head(3, len(raw)) + raw
    if isinstance(value, dict):
        return head(5, len(value)) + b"".join(cbor(k) + cbor(v) for k, v in value.items())
    raise TypeError(type(value))


class Passkey:
    def __init__(self) -> None:
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.id = os.urandom(16)
        self.counter = 0
        self.user = b""

    def _client_data(self, kind: str, challenge: str, origin: str) -> bytes:
        return json.dumps({"type": kind, "challenge": challenge, "origin": origin, "crossOrigin": False}).encode()

    def register(self, options: dict, origin: str) -> dict:
        self.user = unb64url(options["user"]["id"])
        numbers = self.key.public_key().public_numbers()
        cose = cbor({1: 2, 3: -7, -1: 1, -2: numbers.x.to_bytes(32, "big"), -3: numbers.y.to_bytes(32, "big")})
        auth = (
            hashlib.sha256(options["rp"]["id"].encode()).digest() + bytes([0x45]) + struct.pack(">I", 0)
            + bytes(16) + struct.pack(">H", len(self.id)) + self.id + cose
        )
        return {
            "id": b64url(self.id), "rawId": b64url(self.id), "type": "public-key", "clientExtensionResults": {},
            "response": {
                "clientDataJSON": b64url(self._client_data("webauthn.create", options["challenge"], origin)),
                "attestationObject": b64url(cbor({"fmt": "none", "attStmt": {}, "authData": auth})),
                "transports": ["internal"],
            },
        }

    def sign_in(self, options: dict, origin: str) -> dict:
        self.counter += 1
        auth = hashlib.sha256(options["rpId"].encode()).digest() + bytes([0x05]) + struct.pack(">I", self.counter)
        data = self._client_data("webauthn.get", options["challenge"], origin)
        signature = self.key.sign(auth + hashlib.sha256(data).digest(), ec.ECDSA(hashes.SHA256()))
        return {
            "id": b64url(self.id), "rawId": b64url(self.id), "type": "public-key", "clientExtensionResults": {},
            "response": {
                "clientDataJSON": b64url(data), "authenticatorData": b64url(auth), "signature": b64url(signature),
                "userHandle": b64url(self.user),
            },
        }


# -- a stand-in for Google ----------------------------------------------------------------


GOOGLE_CLIENT = "1234-test.apps.googleusercontent.com"
GOOGLE_ISSUER = "https://accounts.google.com"


class FakeGoogle:
    """Serves the signing keys; signs the ID tokens Google would post back."""

    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        n = self.key.public_key().public_numbers()
        jwks = {"keys": [{"kty": "RSA", "kid": "k1", "alg": "RS256", "use": "sig",
                          "n": b64url(n.n.to_bytes(256, "big")), "e": b64url(n.e.to_bytes(3, "big"))}]}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                body = json.dumps(jwks).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "max-age=60")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def id_token(self, *, sub: str, nonce: str, email: str = "me@example.com", aud: str = GOOGLE_CLIENT, kid: str = "k1") -> str:
        header = b64url(json.dumps({"alg": "RS256", "kid": kid, "typ": "JWT"}).encode())
        claims = {"iss": GOOGLE_ISSUER, "aud": aud, "sub": sub, "email": email, "email_verified": True,
                  "nonce": nonce, "iat": int(time.time()), "exp": int(time.time()) + 600}
        body = b64url(json.dumps(claims).encode())
        signature = self.key.sign(f"{header}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())
        return f"{header}.{body}.{b64url(signature)}"

    def close(self) -> None:
        self.server.shutdown()


# -- the Worker, locally --------------------------------------------------------------------


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Cloud:
    def __init__(self, root: Path, google: FakeGoogle, *, invites: bool = True) -> None:
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.state = root / "state"
        assets = root / "dist"
        assets.mkdir()
        (assets / "index.html").write_text("<!doctype html><title>TokenLedger</title>")
        self.conf = {
            "worker": "tokenledger-test", "database": "tokenledger-test", "domain": "127.0.0.1",
            "google_client_id": GOOGLE_CLIENT,
            "invites": invites,
            "vars": {"GOOGLE_JWKS_URL": google.url + "/certs", "GOOGLE_AUTH_URL": google.url + "/auth",
                     "GOOGLE_ISSUER": GOOGLE_ISSUER},
        }
        self.config = cloud_tool.write_config(self.conf, dev=True, assets=assets, path=root / "wrangler.toml")
        env = {**os.environ, "CI": "true", "WRANGLER_SEND_METRICS": "false",
               "XDG_CONFIG_HOME": str(root / "xdg"), "WRANGLER_LOG_PATH": str(root / "logs")}
        self.env = env
        self.wrangler("d1", "migrations", "apply", self.conf["database"], "--local")
        self.proc = subprocess.Popen(
            ["npx", "--no-install", "wrangler", "dev", "--local", "--ip", "127.0.0.1", "--port", str(self.port),
             "--persist-to", str(self.state), "--config", str(self.config)],
            cwd=ROOT / "cloud", env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                urllib.request.urlopen(self.url + "/api/session", timeout=2).close()
                return
            except OSError:
                time.sleep(0.5)
        self.close()
        raise RuntimeError("wrangler dev did not start")

    def wrangler(self, *args: str) -> str:
        out = subprocess.run(
            ["npx", "--no-install", "wrangler", *args, "--persist-to", str(self.state), "--config", str(self.config)],
            cwd=ROOT / "cloud", env=self.env, capture_output=True, text=True,
        )
        assert out.returncode == 0, out.stderr[-2000:]
        return out.stdout

    def code(self, kind: str, user_id: str | None = None) -> str:
        code = base64.urlsafe_b64encode(os.urandom(18)).decode()
        now = datetime.now(timezone.utc)
        user = f"'{user_id}'" if user_id else "NULL"
        self.wrangler(
            "d1", "execute", self.conf["database"], "--local", "--command",
            f"INSERT INTO codes(id, kind, user_id, created_at, expires_at) VALUES('{hashlib.sha256(code.encode()).hexdigest()}', "
            f"'{kind}', {user}, '{now.isoformat()}', '{(now + timedelta(hours=1)).isoformat()}')",
        )
        return code

    def sql(self, query: str) -> list[dict]:
        out = self.wrangler("d1", "execute", self.conf["database"], "--local", "--json", "--command", query)
        return json.loads(out)[0]["results"]

    def close(self) -> None:
        self.proc.terminate()
        self.proc.wait(timeout=20)


@pytest.fixture(scope="module")
def google():
    g = FakeGoogle()
    yield g
    g.close()


@pytest.fixture(scope="module")
def cloud(tmp_path_factory, google):
    c = Cloud(tmp_path_factory.mktemp("cloud"), google)
    yield c
    c.close()


class Browser:
    """A person on the web dashboard: cookies, and this origin on every change."""

    def __init__(self, cloud: Cloud) -> None:
        self.cloud = cloud
        self.cookies: dict[str, str] = {}
        self.passkey = Passkey()

    @property
    def cookie(self) -> str:
        return "; ".join(f"{k}={v}" for k, v in self.cookies.items())

    def call(self, method: str, path: str, payload=None, *, origin: str | None = None, form: dict | None = None) -> tuple[int, dict | bytes]:
        headers = {"Origin": origin or self.cloud.url, "Accept-Encoding": "gzip"}
        if self.cookies:
            headers["Cookie"] = self.cookie
        data = None
        if payload is not None:
            data = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        if form is not None:
            data = urllib.parse.urlencode(form).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        request = urllib.request.Request(self.cloud.url + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=30) as resp:
                status, h, body = resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as exc:
            status, h, body = exc.code, exc.headers, exc.read()
        for cookie in h.get_all("Set-Cookie") or []:
            name, _, value = cookie.split(";", 1)[0].partition("=")
            if "Max-Age=0" in cookie:
                self.cookies.pop(name, None)
            else:
                self.cookies[name] = value
        if h.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
        try:
            return status, json.loads(body)
        except ValueError:
            return status, body

    def register(self, code: str | None, name: str | None = None) -> tuple[int, dict]:
        status, start = self.call("POST", "/api/passkey/register/options", {"code": code, "name": name})
        if status != 200:
            return status, start
        response = self.passkey.register(start["options"], self.cloud.url)
        return self.call("POST", "/api/passkey/register/verify", {"challenge_id": start["challenge_id"], "response": response})

    def google(self, fake: FakeGoogle, *, sub: str, code=None, name=None, next=None, tamper=None) -> str:
        """Sign in with Google: start, "Google" answers by posting an ID token back; returns where
        the answer page sends the browser."""
        status, start = self.call("POST", "/api/auth/google/start", {"code": code, "name": name, "next": next})
        assert status == 200, start
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(start["url"]).query))
        assert query["client_id"] == GOOGLE_CLIENT and query["redirect_uri"] == self.cloud.url + "/api/auth/google/callback"
        form = {"id_token": fake.id_token(sub=sub, nonce=query["nonce"]), "state": query["state"]}
        if tamper:
            tamper(form, query)
        status, page = self.call("POST", "/api/auth/google/callback", form=form, origin="https://accounts.google.com")
        assert status == 200
        return urllib.parse.unquote(page.decode().split("url=", 1)[1].split('"', 1)[0].replace("&#38;", "&"))

    def sign_in(self) -> tuple[int, dict]:
        status, start = self.call("POST", "/api/passkey/login/options", {})
        response = self.passkey.sign_in(start["options"], self.cloud.url)
        return self.call("POST", "/api/passkey/login/verify", {"challenge_id": start["challenge_id"], "response": response})


def machine(tmp_path: Path, name: str, tz: str = "Europe/London") -> tuple[Paths, Settings, object]:
    paths = Paths(data=tmp_path / name)
    settings = Settings(timezone=tz, grace_days=3, machine=Machine(name, name.title(), "linux"))
    save_settings(paths, settings)
    conn = connect(paths.db)
    migrate(conn)
    return paths, settings, conn


def sign_machine_in(cloud: Cloud, browser: Browser, paths: Paths, settings: Settings, conn) -> dict:
    """`tokenledger sync login` on the machine, approved in the browser meanwhile."""
    codes: list[str] = []
    result: dict = {}

    def say(line: str) -> None:
        if line.startswith("Check"):
            codes.append(line.rsplit(" ", 1)[1].rstrip("."))

    def run() -> None:
        try:
            result["ok"] = client.login(conn_thread(paths), paths, settings, cloud.url, open_browser=False, say=say)
        except Exception as exc:  # noqa: BLE001
            result["error"] = exc

    thread = threading.Thread(target=run)
    thread.start()
    deadline = time.monotonic() + 20
    while not codes and time.monotonic() < deadline:
        time.sleep(0.1)
    status, info = browser.call("GET", f"/api/link/request?code={codes[0]}")
    assert status == 200 and info["machine"]["id"] == settings.machine.id
    status, _ = browser.call("POST", "/api/link/approve", {"code": codes[0]})
    assert status == 200
    thread.join(timeout=30)
    if "error" in result:
        raise result["error"]
    return result["ok"]


def conn_thread(paths: Paths):
    conn = connect(paths.db)
    migrate(conn)
    return conn


# -- the tests ------------------------------------------------------------------------------


def test_people_and_machines_end_to_end(cloud, tmp_path):
    me = Browser(cloud)
    status, session = me.call("GET", "/api/session")
    assert session == {"mode": "cloud", "user": None, "setup_needed": True, "google": True, "invites": True}
    assert me.register("not-a-code", "me")[0] == 403
    setup = cloud.code("setup")
    status, created = me.register(setup, "me")
    assert status == 200 and created["name"] == "me"
    assert me.call("GET", "/api/session")[1]["user"] == {"name": "me", "admin": True}
    assert Browser(cloud).register(setup, "again")[0] == 403  # a code works once

    # Signing in again with the passkey, in a fresh browser.
    again = Browser(cloud)
    again.passkey = me.passkey
    assert again.sign_in()[0] == 200 and again.call("GET", "/api/session")[1]["user"]["name"] == "me"

    # Two machines. The second has an empty ledger in another timezone: it adopts the first's.
    a_paths, a_settings, a = machine(tmp_path, "alpha")
    b_paths, b_settings, b = machine(tmp_path, "beta", tz="Asia/Shanghai")
    sign_machine_in(cloud, me, a_paths, a_settings, a)
    capture_into(a, a_settings, SeriesKey("claude-logs", "claude", "alpha", Scope.MACHINE),
                 [usage("2026-10-10", 100, cost=5)], "2026-10-15T10:00:00")
    first = client.sync(a, a_paths, a_settings)
    assert first["ok"] and first["shared"]["bytes"] > 0 and first["dashboard"]["bytes"] > 0, first

    login = sign_machine_in(cloud, me, b_paths, b_settings, b)
    assert login["adopted"] == {"timezone": "Europe/London", "grace_days": 3}
    b_settings = load_settings(b_paths)
    capture_into(b, b_settings, SeriesKey("claude-logs", "claude", "beta", Scope.MACHINE),
                 [usage("2026-10-10", 40, cost=2)], "2026-10-15T11:00:00")
    synced = client.sync(b, b_paths, b_settings)
    assert synced["ok"] and [m["machine"] for m in synced["merged"]] == ["Alpha"], synced
    assert client.sync(a, a_paths, a_settings)["merged"][0]["machine"] == "Beta"
    for conn in (a, b):
        total = conn.execute("SELECT SUM(total) FROM ledger WHERE day = '2026-10-10' AND model = '*'").fetchone()[0]
        assert total == 140

    # The web dashboard shows both machines.
    status, view = me.call("GET", "/api/view?range=all")
    assert status == 200 and view["summary"]["tokens"] == 140
    assert {m["label"] for m in view["machines"]["machines"]} == {"Alpha", "Beta"}
    status, devices = me.call("GET", "/api/devices")
    assert {d["label"] for d in devices["devices"]} == {"Alpha", "Beta"}

    # A device token works only for its own machine, ledger and computer.
    link = sync_config.load(a_paths)
    session = client.Session(link, a)
    for header, value, code in [
        ("X-TokenLedger-Machine", "beta", 403),
        ("X-TokenLedger-Host", "f" * 32, 403),
        ("X-TokenLedger-Instance", "recreated", 409),
    ]:
        status, _, _ = client._request(cloud.url, "GET", "/api/sync/manifest", headers={**session.headers, header: value})
        if header == "X-TokenLedger-Host" and not session.headers["X-TokenLedger-Host"]:
            continue  # this computer offers no id to check
        assert status == code, header

    # Signing a machine out on the web: its next sync says so.
    beta = next(d for d in devices["devices"] if d["label"] == "Beta")
    assert me.call("DELETE", f"/api/devices/{beta['id']}")[0] == 200
    failed = client.sync(b, b_paths, b_settings)
    assert not failed["ok"] and "signed out" in failed["error"]
    assert client.status(b_paths)["signed_out"]

    # Cross-site changes are refused; nothing works without a session.
    assert me.call("POST", "/api/invites", {}, origin="https://evil.example")[0] == 403
    assert Browser(cloud).call("GET", "/api/view?range=all")[0] == 401

    # Someone invited sees only their own (empty) ledger.
    status, invite = me.call("POST", "/api/invites", {})
    friend = Browser(cloud)
    assert friend.register(invite["url"].split("#", 1)[1], "me")[0] == 409  # names are unique
    status, invite = me.call("POST", "/api/invites", {})
    assert friend.register(invite["url"].split("#", 1)[1], "friend")[0] == 200
    assert friend.call("GET", "/api/session")[1]["user"] == {"name": "friend", "admin": False}
    assert friend.call("GET", "/api/view?range=all")[0] == 404
    assert friend.call("GET", "/api/devices")[1] == {"devices": []}
    assert friend.call("POST", "/api/invites", {})[0] == 403
    f_paths, f_settings, f = machine(tmp_path, "gamma")
    sign_machine_in(cloud, friend, f_paths, f_settings, f)
    assert client.Session(sync_config.load(f_paths), f).json("GET", "/api/sync/manifest")["snapshots"] == []

    # A recovery code adds a passkey to an account that lost its own.
    user_id = cloud.sql("SELECT id FROM users WHERE name = 'me'")[0]["id"]
    lost = Browser(cloud)
    assert lost.register(cloud.code("recover", user_id))[0] == 200
    assert lost.call("GET", "/api/session")[1]["user"]["name"] == "me"
    assert len(lost.call("GET", "/api/passkeys")[1]["passkeys"]) == 2

    # Security headers on every answer.
    with urllib.request.urlopen(cloud.url + "/api/session") as resp:
        assert "frame-ancestors 'none'" in resp.headers["Content-Security-Policy"]
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
    for conn in (a, b, f):
        conn.close()


def test_signing_in_with_google(cloud, google):
    # An invitation, accepted with a Google account: the account is created and signed in.
    alice = Browser(cloud)
    assert alice.google(google, sub="g-alice", code=cloud.code("invite"), name="alice") == "/"
    assert alice.call("GET", "/api/session")[1]["user"] == {"name": "alice", "admin": False}
    assert [i["email"] for i in alice.call("GET", "/api/identities")[1]["identities"]] == ["me@example.com"]

    # Signing in again elsewhere, and coming back to the page that asked for it.
    again = Browser(cloud)
    assert again.google(google, sub="g-alice", next="/link?code=ABCD-EFGH") == "/link?code=ABCD-EFGH"
    assert again.call("GET", "/api/session")[1]["user"]["name"] == "alice"

    # A Google account nobody linked cannot sign in.
    stranger = Browser(cloud)
    assert "not linked" in stranger.google(google, sub="g-stranger")
    assert stranger.call("GET", "/api/session")[1]["user"] is None

    # An answer that does not belong to this attempt, or is not from Google, changes nothing.
    def wrong_nonce(form, query):
        form["id_token"] = google.id_token(sub="g-alice", nonce="other")

    def wrong_audience(form, query):
        form["id_token"] = google.id_token(sub="g-alice", nonce=query["nonce"], aud="someone-else")

    def forged(form, query):
        head, body, _ = form["id_token"].split(".")
        form["id_token"] = f"{head}.{body}.{b64url(b'0' * 256)}"

    def other_state(form, query):
        form["state"] = "made-up"

    for tamper, words in [(wrong_nonce, "does not belong"), (wrong_audience, "another application"),
                          (forged, "signature"), (other_state, "expired or was started elsewhere")]:
        b = Browser(cloud)
        assert words in b.google(google, sub="g-alice", tamper=tamper), words
        assert b.call("GET", "/api/session")[1]["user"] is None

    # A recovery link links a new Google account to the existing one.
    user_id = cloud.sql("SELECT id FROM users WHERE name = 'alice'")[0]["id"]
    new_phone = Browser(cloud)
    assert new_phone.google(google, sub="g-alice-2", code=cloud.code("recover", user_id)) == "/"
    assert new_phone.call("GET", "/api/session")[1]["user"]["name"] == "alice"
    # One Google account belongs to one person.
    assert "linked to another account" in Browser(cloud).google(google, sub="g-alice", code=cloud.code("invite"), name="bob")


def test_a_ledger_is_one_persons_unless_invitations_are_on(tmp_path_factory, google):
    private = Cloud(tmp_path_factory.mktemp("private"), google, invites=False)
    try:
        me = Browser(private)
        assert me.call("GET", "/api/session")[1]["invites"] is False
        assert me.google(google, sub="g-owner", code=private.code("setup"), name="owner") == "/"
        assert me.call("POST", "/api/invites", {})[0] == 403
        # Even a code made by hand in the database does not let anyone else in.
        status, answer = Browser(private).call("POST", "/api/auth/google/start", {"code": private.code("invite"), "name": "x"})
        assert status == 403 and "invitations are off" in answer["error"]
        assert "not linked" in Browser(private).google(google, sub="g-someone-else")
    finally:
        private.close()
