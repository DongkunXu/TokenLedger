"""Codex account-wide daily token totals, read from OpenAI's servers.

Starts the Codex CLI (the one bundled with the ChatGPT app, else ``codex`` on
PATH) as ``codex app-server``,
sends ``initialize`` and one read-only ``account/usage/read`` request over
stdio (JSON-RPC, one message per line), then shuts it down. The response holds
one token total per day for the whole account (all devices), with no split by
token type and no cost. Day keys are the server's own ``startDate`` values.
"""

from __future__ import annotations

import json
import os
import select
import shutil
import subprocess
import tempfile
import time

from .. import __version__
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

    def __init__(self, candidates: tuple[str, ...] = BINARY_CANDIDATES) -> None:
        self.candidates = candidates

    def collect(self, ctx: SourceContext) -> list[CaptureResult]:
        binary = find_binary(self.candidates)
        version = run_tool([binary, "--version"], timeout=30).stdout.decode().strip() or "unknown"
        response = request(binary, "account/usage/read", None)
        raw = json.dumps(response, indent=1).encode()
        return [parse_usage_response(response, raw=raw, tool_version=version)]


def parse_usage_response(response: dict, *, raw: bytes, tool_version: str) -> CaptureResult:
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
        series=SeriesKey("codex-server", "codex", "account", Scope.ACCOUNT),
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
    buffer = b""

    def send(message: dict) -> None:
        proc.stdin.write(json.dumps(message).encode() + b"\n")
        proc.stdin.flush()

    def read_response(want_id: int, deadline: float) -> dict:
        nonlocal buffer
        while time.monotonic() < deadline:
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if message.get("id") == want_id and ("result" in message or "error" in message):
                    return message
            ready, _, _ = select.select([proc.stdout], [], [], 0.5)
            if ready:
                chunk = os.read(proc.stdout.fileno(), 65536)
                if not chunk:
                    raise SourceError(f"codex app-server exited early: {_tail(errlog)}")
                buffer += chunk
        raise SourceError(f"codex app-server did not answer {method} within {timeout:.0f}s")

    try:
        deadline = time.monotonic() + timeout
        send({"method": "initialize", "id": 0, "params": {"clientInfo": {"name": "tokenledger", "title": None, "version": __version__}}})
        init = read_response(0, deadline)
        if "error" in init:
            raise SourceError(f"codex app-server initialize failed: {init['error']}")
        send({"method": "initialized"})
        message: dict = {"method": method, "id": 1}
        if params is not None:
            message["params"] = params
        send(message)
        return read_response(1, deadline)
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
