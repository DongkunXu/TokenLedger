"""CodexBar's quota history: account-wide used-percent snapshots per limit window.

CodexBar samples each provider's limit windows (5-hour session, weekly, ...)
and keeps them in ``history/<provider>.json``. That file is capped and
compacted over time, so the ledger copies every sample it has not seen yet.
The files are read, never written.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..models import QuotaCapture, QuotaSample
from .base import SourceContext, SourceError

DEFAULT_DIR = Path.home() / "Library/Application Support/com.steipete.codexbar/history"
UNSCOPED = "unscoped"


class CodexBarQuotaSource:
    id = "codexbar-quota"

    def __init__(self, directory: Path = DEFAULT_DIR) -> None:
        self.directory = directory

    def collect(self, ctx: SourceContext) -> QuotaCapture:
        if not self.directory.is_dir():
            raise SourceError(f"CodexBar history folder not found: {self.directory}")
        samples: list[QuotaSample] = []
        for path in sorted(self.directory.glob("*.json")):
            samples.extend(parse_history_file(path.stem, path.read_bytes()))
        return QuotaCapture(source=self.id, samples=samples, tool_version="codexbar-history", raw_name="", raw=b"")


def parse_history_file(provider: str, raw: bytes) -> list[QuotaSample]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SourceError(f"history/{provider}.json is not JSON") from exc
    groups: list[tuple[str, list]] = [(acct, wins) for acct, wins in (payload.get("accounts") or {}).items()]
    if payload.get("unscoped"):
        groups.append((UNSCOPED, payload["unscoped"]))
    samples = []
    for account, windows in groups:
        for window in windows:
            name = window.get("name")
            if not isinstance(name, str):
                raise SourceError(f"history/{provider}.json: window without name")
            for entry in window.get("entries") or []:
                captured, used = entry.get("capturedAt"), entry.get("usedPercent")
                if not isinstance(captured, str) or not isinstance(used, (int, float)):
                    raise SourceError(f"history/{provider}.json: malformed entry {entry!r}")
                samples.append(
                    QuotaSample(
                        provider=provider,
                        account=account,
                        window=name,
                        window_minutes=window.get("windowMinutes"),
                        captured_at=captured,
                        used_percent=float(used),
                        resets_at=entry.get("resetsAt"),
                        source="codexbar-history",
                    )
                )
    return samples
