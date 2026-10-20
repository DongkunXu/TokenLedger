"""Codex account-wide daily token totals, read from OpenAI's servers.

Starts the Codex CLI (the one bundled with the ChatGPT app, the standalone or npm
install, else ``codex`` on PATH) as ``codex app-server``, sends ``initialize``
and two read-only requests over stdio (JSON-RPC, one message per line), then
shuts it down:

* ``account/read``: which ChatGPT account is signed in. Its account id, hashed,
  names the series (``acct-<hash>``), so several machines signed in to the same
  account write one series, and different accounts are added up. The email, hashed,
  is the fallback when no id is returned; only the hashes are kept
* ``account/usage/read``: one token total per day for the whole account (all
  devices), with no split by token type and no cost. Day keys are the server's
  own ``startDate`` values

Which series a reading goes to is decided in ``capture.py`` (``_account_series``).
Ledgers from before accounts were told apart have a series named ``account``;
the first capture that knows the account folds it into that account's series
(``repo.merge_series``).
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import sys
import time
from pathlib import Path

from .. import __version__, homes
from ..models import DAY_TOTAL, CaptureResult, CostQuality, DayUsage, Scope, SeriesKey
from .base import SourceContext, SourceError, clean_env, run_tool

# Where the ChatGPT app has shipped the Codex CLI, newest layout first. The app moves it
# between releases, so each known location is tried in turn; a standalone ``codex`` on
# PATH is the fallback.
BINARY_CANDIDATES = (
    "/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex",  # ChatGPT 26.924+ (launcher script)
    "/Applications/ChatGPT.app/Contents/Resources/codex",  # earlier releases
)
TIMEOUT_S = 90.0
LEGACY_ACCOUNT = "account"  # series name before accounts were told apart


def default_candidates() -> tuple[str, ...]:
    """Known install locations on this operating system. On Windows the native ``codex.exe``
    comes first: through the npm ``codex.cmd`` shim, stopping the server would only stop cmd.exe."""
    if sys.platform == "darwin":
        return BINARY_CANDIDATES
    home = Path.home()
    if sys.platform == "win32":
        local = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
        roaming = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
        npm = roaming / "npm" / "node_modules" / "@openai" / "codex" / "node_modules" / "@openai"
        return (
            str(local / "Programs" / "OpenAI" / "Codex" / "bin" / "codex.exe"),  # standalone installer
            str(npm / "codex-win32-x64" / "vendor" / "x86_64-pc-windows-msvc" / "bin" / "codex.exe"),
            str(npm / "codex-win32-arm64" / "vendor" / "aarch64-pc-windows-msvc" / "bin" / "codex.exe"),
        )
    return (str(home / ".local" / "bin" / "codex"),)


def account_keys(response: dict) -> dict[str, str]:
    """Names for the signed-in account, as hashes (never the id or email themselves):
    ``id`` from its ChatGPT account id, ``email`` from its email. Either may be missing,
    for example when ``account/read`` fails or Codex is signed in with an API key;
    ``capture.py`` picks the series from them."""
    result = response.get("result") or {}
    account = result.get("account") or {}
    routing = result.get("workspaceRouting") or {}
    keys = {}
    account_id = routing.get("chatgptAccountId")
    if isinstance(account_id, str) and account_id.strip():
        keys["id"] = _hashed("codex-account-id", account_id.strip().lower())
    email = account.get("email") if account.get("type") == "chatgpt" else None
    if isinstance(email, str) and email.strip():
        keys["email"] = _hashed("codex-account", email.strip().lower())
    return keys


def _hashed(kind: str, value: str) -> str:
    return "acct-" + hashlib.sha256(f"tokenledger/{kind}|{value}".encode()).hexdigest()[:16]


def find_binary(candidates: tuple[str, ...] = BINARY_CANDIDATES, fallback: str | None = "codex") -> str:
    for path in candidates:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    if fallback and (path := shutil.which(fallback)):
        return path
    looked = [*candidates, f"{fallback} on PATH"] if fallback else list(candidates)
    raise SourceError("Codex CLI not found; looked at: " + ", ".join(looked))


class CodexServerSource:
    id = "codex-server"

    def __init__(self, candidates: tuple[str, ...] | None = None) -> None:
        self.candidates = candidates if candidates is not None else default_candidates()

    def collect(self, ctx: SourceContext) -> list[CaptureResult]:
        try:
            binary = find_binary(self.candidates)
        except SourceError:
            if not homes.codex_homes(wsl="off"):
                return []  # Codex has never run on this machine: nothing to read, nothing wrong
            raise
        version = run_tool([binary, "--version"], timeout=30).stdout.decode().strip() or "unknown"
        identity, response = requests(binary, [("account/read", {"refreshToken": False}), ("account/usage/read", None)])
        raw = json.dumps(response, indent=1).encode()
        result = parse_usage_response(response, raw=raw, tool_version=version)
        result.meta["account_keys"] = account_keys(identity)
        return [result]


def parse_usage_response(response: dict, *, raw: bytes, tool_version: str, account: str = LEGACY_ACCOUNT) -> CaptureResult:
    if "result" not in response:
        raise SourceError(f"account/usage/read failed: {response.get('error')}")
    result = response["result"]
    buckets = result.get("dailyUsageBuckets")
    if not isinstance(buckets, list) or not buckets:
        raise SourceError("account/usage/read returned no daily buckets")
    rows = []
    for b in buckets:
        day, tokens = b.get("startDate"), b.get("tokens")
        if not isinstance(day, str) or len(day) != 10 or not isinstance(tokens, int) or tokens < 0:
            raise SourceError(f"malformed daily bucket {b!r}")
        rows.append(DayUsage(day=day, model=DAY_TOTAL, total=tokens, cost_quality=CostQuality.NONE))
    days = sorted(r.day for r in rows)
    return CaptureResult(
        series=SeriesKey("codex-server", "codex", account, Scope.ACCOUNT),
        rows=rows,
        window_start=days[0],
        window_end=days[-1],
        tool_version=tool_version,
        raw_name="codex-server-usage",
        raw=raw,
        meta={"summary": result.get("summary")},
    )


def request(binary: str, method: str, params: dict | None, *, timeout: float = TIMEOUT_S) -> dict:
    """Run one JSON-RPC request against a short-lived ``codex app-server``."""
    return requests(binary, [(method, params)], timeout=timeout)[0]


def requests(binary: str, calls: list[tuple[str, dict | None]], *, timeout: float = TIMEOUT_S) -> list[dict]:
    """Run several JSON-RPC requests, one after another, against one short-lived ``codex app-server``.

    stdout is read by a thread (pipes cannot be polled with select on Windows); each
    line is one JSON message.
    """
    # stderr goes to a temp file: a pipe nobody drains could fill up and stall the server.
    errlog = tempfile.TemporaryFile()
    proc = subprocess.Popen(
        [binary, "app-server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=errlog,
        env=clean_env(),
    )
    assert proc.stdin and proc.stdout
    lines: queue.Queue[bytes | None] = queue.Queue()

    def pump() -> None:
        for line in proc.stdout:  # type: ignore[union-attr]
            lines.put(line)
        lines.put(None)

    threading.Thread(target=pump, name="codex-app-server", daemon=True).start()

    def send(message: dict) -> None:
        proc.stdin.write(json.dumps(message).encode() + b"\n")
        proc.stdin.flush()

    def read_response(want_id: int, deadline: float, method: str) -> dict:
        while (left := deadline - time.monotonic()) > 0:
            try:
                line = lines.get(timeout=min(left, 0.5))
            except queue.Empty:
                continue
            if line is None:
                raise SourceError(f"codex app-server exited early: {_tail(errlog)}")
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message.get("id") == want_id and ("result" in message or "error" in message):
                return message
        raise SourceError(f"codex app-server did not answer {method} within {timeout:.0f}s")

    try:
        deadline = time.monotonic() + timeout
        send({"method": "initialize", "id": 0, "params": {"clientInfo": {"name": "tokenledger", "title": None, "version": __version__}}})
        init = read_response(0, deadline, "initialize")
        if "error" in init:
            raise SourceError(f"codex app-server initialize failed: {init['error']}")
        send({"method": "initialized"})
        out = []
        for i, (method, params) in enumerate(calls, start=1):
            message: dict = {"method": method, "id": i}
            if params is not None:
                message["params"] = params
            send(message)
            out.append(read_response(i, deadline, method))
        return out
    finally:
        try:
            proc.stdin.close()
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        errlog.close()


def _tail(errlog) -> str:
    errlog.seek(0)
    return errlog.read().decode(errors="replace").strip()[-400:]
