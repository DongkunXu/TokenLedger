"""Codex account-wide daily token totals, read from OpenAI's servers.

Starts the Codex binary bundled with the ChatGPT app as ``codex app-server``,
sends ``initialize`` and one read-only ``account/usage/read`` request over
stdio (JSON-RPC, one message per line), then shuts it down. The response holds
one token total per day for the whole account (all devices), with no split by
token type and no cost. Day keys are the server's own ``startDate`` values.
"""

from __future__ import annotations

import json
import os
import select
import subprocess
import tempfile
import time

from .. import __version__
from ..models import DAY_TOTAL, CaptureResult, CostQuality, DayUsage, Scope, SeriesKey
from .base import SourceContext, SourceError, clean_env, run_tool

DEFAULT_BINARY = "/Applications/ChatGPT.app/Contents/Resources/codex"
TIMEOUT_S = 90.0


class CodexServerSource:
    id = "codex-server"

    def __init__(self, binary: str = DEFAULT_BINARY) -> None:
        self.binary = binary

    def collect(self, ctx: SourceContext) -> list[CaptureResult]:
        if not os.access(self.binary, os.X_OK):
            raise SourceError(f"Codex binary not found at {self.binary}")
        version = run_tool([self.binary, "--version"], timeout=30).stdout.decode().strip() or "unknown"
        response = request(self.binary, "account/usage/read", None)
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
