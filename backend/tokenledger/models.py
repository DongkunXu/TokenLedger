"""Core data structures shared by sources, the ledger, and the views."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

DAY_TOTAL = "*"  # model name used for a day's all-model total row


class Scope(StrEnum):
    MACHINE = "machine"  # read from this machine's local logs
    ACCOUNT = "account"  # reported by the provider for the whole account


class CostQuality(StrEnum):
    LIST_PRICE = "list_price"  # computed by CodexBar at API list prices
    RECOMPUTED = "recomputed"  # recomputed by TokenLedger with CodexBar's pricing rules
    UNPRICED = "unpriced"  # tokens known, model has no price
    NONE = "none"  # the source reports no cost at all


class State(StrEnum):
    OPEN = "open"
    FINAL = "final"


@dataclass(frozen=True)
class SeriesKey:
    source: str  # e.g. "codexbar", "codex-server"
    provider: str  # e.g. "claude", "codex", "antigravity"
    machine: str  # machine id, or "account" for account-wide series
    scope: Scope


@dataclass(frozen=True)
class DayUsage:
    """Usage for one day and one model (``DAY_TOTAL`` for the all-model row).

    Token fields are normalized: ``input_uncached`` never includes cache reads,
    and ``total`` is the provider's own notion of total tokens. Fields a source
    does not report are ``None``. ``reasoning`` is informational and already
    counted inside ``output``.
    """

    day: str  # YYYY-MM-DD in the ledger timezone (or the source's own day key)
    model: str
    total: int
    input_uncached: int | None = None
    cache_read: int | None = None
    cache_write: int | None = None
    output: int | None = None
    reasoning: int | None = None
    cost_micros: int | None = None  # USD * 1e6
    cost_quality: CostQuality = CostQuality.NONE

    def __post_init__(self) -> None:
        priced = self.cost_quality in (CostQuality.LIST_PRICE, CostQuality.RECOMPUTED)
        if priced != (self.cost_micros is not None):
            raise ValueError(
                f"{self.day}/{self.model}: cost {self.cost_micros!r} does not match quality {self.cost_quality}"
            )
        if self.total < 0:
            raise ValueError(f"{self.day}/{self.model}: negative total")

    def values(self) -> tuple:
        return (
            self.total,
            self.input_uncached,
            self.cache_read,
            self.cache_write,
            self.output,
            self.reasoning,
            self.cost_micros,
            str(self.cost_quality),
        )


@dataclass
class CaptureResult:
    """Everything one source run produced for one series."""

    series: SeriesKey
    rows: list[DayUsage]
    window_start: str | None  # first day the source covers; days before it are not judged missing
    window_end: str | None
    tool_version: str
    raw_name: str  # file name stem for the raw archive
    raw: bytes  # the source's untouched output
    meta: dict = field(default_factory=dict)


@dataclass(frozen=True)
class QuotaSample:
    provider: str
    account: str
    window: str  # e.g. "session", "weekly"
    window_minutes: int | None
    captured_at: str  # ISO-8601 UTC
    used_percent: float
    resets_at: str | None
    source: str


@dataclass
class QuotaCapture:
    source: str
    samples: list[QuotaSample]
    tool_version: str
    raw_name: str
    raw: bytes
