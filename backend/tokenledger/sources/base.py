"""The interface every data source implements.

A source reads one external system and returns ``CaptureResult`` objects (daily
usage for one series each) or a ``QuotaCapture``. It never touches the ledger
database; ``capture.run`` does that. New platforms (for example the Windows
Claude Code log parser) are added as new sources.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from ..config import Settings
from ..models import CaptureResult, QuotaCapture


class SourceError(RuntimeError):
    """A source could not produce trustworthy data. The capture is recorded as failed."""


@dataclass(frozen=True)
class SourceContext:
    settings: Settings
    now_utc: datetime


class UsageSource(Protocol):
    id: str

    def collect(self, ctx: SourceContext) -> list[CaptureResult]: ...


class QuotaSource(Protocol):
    id: str

    def collect(self, ctx: SourceContext) -> QuotaCapture: ...


# Variables that redirect CodexBar or Codex to other data folders. CodexBar keeps a single
# shared cache, so running its CLI with a different root rewrites the menu-bar app's cache.
# Sources always run external tools with these removed, exactly like the menu-bar app sees it.
REDIRECTING_ENV = ("CODEX_HOME", "CLAUDE_CONFIG_DIR", "CODEXBAR_CONFIG")


def clean_env() -> dict[str, str]:
    env = dict(os.environ)
    for name in REDIRECTING_ENV:
        env.pop(name, None)
    return env


def run_tool(args: list[str], *, timeout: float) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(args, capture_output=True, timeout=timeout, env=clean_env(), check=False)
    except FileNotFoundError as exc:
        raise SourceError(f"{args[0]} not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise SourceError(f"{' '.join(args[:3])} timed out after {timeout:.0f}s") from exc
