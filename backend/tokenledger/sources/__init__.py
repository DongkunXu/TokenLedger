"""Data sources. ``default_usage_sources`` lists what runs on this machine.

CodexBar (Claude and Antigravity cost, quota history) is a macOS app; its
command line tool also exists for Linux. On machines without it, Claude is
read from its logs by ``claude-logs`` (the same counting rules), and
Antigravity is not available.
Every machine reads Codex from its own logs and from OpenAI's servers.
"""

from __future__ import annotations

import shutil
import sys

from .base import QuotaSource, SourceContext, SourceError, UsageSource
from .claude_logs import ClaudeLogsSource
from .codex_logs import CodexLogsSource
from .codex_server import CodexServerSource
from .codexbar import CodexBarCostSource
from .codexbar_quota import CodexBarQuotaSource


def codexbar_expected() -> bool:
    """CodexBar is a requirement on macOS; elsewhere it is used when its CLI is installed."""
    return sys.platform == "darwin" or shutil.which("codexbar") is not None


def default_usage_sources() -> list[UsageSource]:
    # Claude comes from one reader per machine: CodexBar where it is expected (it also
    # covers Antigravity), claude-logs, which counts the same way, everywhere else.
    claude: list[UsageSource] = [CodexBarCostSource()] if codexbar_expected() else [ClaudeLogsSource()]
    return [*claude, CodexLogsSource(), CodexServerSource()]


def default_quota_sources() -> list[QuotaSource]:
    return [CodexBarQuotaSource()] if sys.platform == "darwin" else []


def all_usage_sources() -> list[UsageSource]:
    return [CodexBarCostSource(), ClaudeLogsSource(), CodexLogsSource(), CodexServerSource()]


def usage_source_by_id(source_id: str) -> UsageSource:
    for source in all_usage_sources():
        if source.id == source_id:
            return source
    raise KeyError(source_id)


__all__ = [
    "ClaudeLogsSource",
    "CodexBarCostSource",
    "CodexBarQuotaSource",
    "CodexLogsSource",
    "CodexServerSource",
    "QuotaSource",
    "SourceContext",
    "SourceError",
    "UsageSource",
    "all_usage_sources",
    "default_quota_sources",
    "default_usage_sources",
    "usage_source_by_id",
]
