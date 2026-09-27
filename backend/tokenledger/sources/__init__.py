"""Data sources. ``default_sources`` lists what runs on this machine."""

from __future__ import annotations

from .base import QuotaSource, SourceContext, SourceError, UsageSource
from .codex_server import CodexServerSource
from .codexbar import CodexBarCostSource
from .codexbar_quota import CodexBarQuotaSource


def default_usage_sources() -> list[UsageSource]:
    return [CodexBarCostSource(), CodexServerSource()]


def default_quota_sources() -> list[QuotaSource]:
    return [CodexBarQuotaSource()]


def usage_source_by_id(source_id: str) -> UsageSource:
    for source in default_usage_sources():
        if source.id == source_id:
            return source
    raise KeyError(source_id)


__all__ = [
    "CodexBarCostSource",
    "CodexBarQuotaSource",
    "CodexServerSource",
    "QuotaSource",
    "SourceContext",
    "SourceError",
    "UsageSource",
    "default_quota_sources",
    "default_usage_sources",
    "usage_source_by_id",
]
