"""Paths and persistent settings.

Settings live in ``<data>/settings.json`` and are created on first use. The
timezone decides how days are bucketed; it must match CodexBar's own
``tokenCostUsageBucketTimeZone`` so that a day means the same thing on both
sides. On first use it is taken from CodexBar, or from this computer when
CodexBar has no setting. The machine id is generated once and identifies this
computer's series.
"""

from __future__ import annotations

import json
import os
import platform
import socket
import subprocess
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

CODEXBAR_DEFAULTS = "com.steipete.codexbar"
CODEXBAR_TIMEZONE_KEY = "tokenCostUsageBucketTimeZone"
DEFAULT_GRACE_DAYS = 3
DEFAULT_AUTO_CAPTURE_MINUTES = 60


def project_root() -> Path:
    env = os.environ.get("TOKENLEDGER_ROOT")
    if env:
        return Path(env)
    # backend/tokenledger/config.py -> project root is three levels up
    return Path(__file__).resolve().parents[2]


def run_dir() -> Path:
    """Runtime state of this machine's processes (dashboard pid file, log). Not user data."""
    env = os.environ.get("TOKENLEDGER_RUN_DIR")
    return Path(env) if env else project_root() / ".build" / "run"


def data_dir() -> Path:
    env = os.environ.get("TOKENLEDGER_DATA_DIR")
    return Path(env) if env else project_root() / "data"


@dataclass(frozen=True)
class Paths:
    data: Path

    @property
    def db(self) -> Path:
        return self.data / "ledger.sqlite"

    @property
    def raw(self) -> Path:
        return self.data / "raw"

    @property
    def settings(self) -> Path:
        return self.data / "settings.json"


@dataclass(frozen=True)
class Machine:
    id: str
    label: str
    os: str


@dataclass(frozen=True)
class Settings:
    timezone: str
    grace_days: int
    machine: Machine
    wsl: str = "all"  # Windows: read WSL distributions' logs: "all", "running" (no starting) or "off"
    auto_capture_minutes: int = DEFAULT_AUTO_CAPTURE_MINUTES  # while the dashboard server runs; 0 = off

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


def codexbar_timezone() -> str | None:
    """The timezone CodexBar buckets days in, if it has one set (macOS only)."""
    try:
        out = subprocess.run(
            ["defaults", "read", CODEXBAR_DEFAULTS, CODEXBAR_TIMEZONE_KEY], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    tz = out.stdout.strip()
    return tz if out.returncode == 0 and tz else None


def system_timezone() -> str:
    """This computer's IANA timezone name, or UTC when it cannot be determined.

    tzlocal also maps Windows timezone names (e.g. "China Standard Time") to IANA ones;
    the ``tzdata`` package provides the timezone database Windows does not have.
    """
    name = os.environ.get("TZ", "").lstrip(":")
    if not name:
        try:
            import tzlocal

            name = tzlocal.get_localzone_name() or ""
        except Exception:  # noqa: BLE001 - fall back to reading /etc/localtime below
            name = ""
    if not name:
        target = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in target:
            name = target.split("zoneinfo/", 1)[1]
    try:
        ZoneInfo(name)
    except (ValueError, KeyError):  # empty, malformed or unknown name
        return "UTC"
    return name


def _computer_name() -> str:
    if platform.system() == "Darwin":
        try:
            out = subprocess.run(["scutil", "--get", "ComputerName"], capture_output=True, text=True, timeout=5)
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
    return socket.gethostname().split(".")[0]


def _new_machine() -> Machine:
    label = _computer_name()
    slug = "".join(ch if ch.isalnum() else "-" for ch in label.lower()).strip("-") or "machine"
    return Machine(id=f"{slug[:24]}-{uuid.uuid4().hex[:8]}", label=label, os=platform.system().lower())


def load_settings(paths: Paths) -> Settings:
    """Load settings, creating the file with defaults on first use."""
    if paths.settings.exists():
        raw = json.loads(paths.settings.read_text())
        settings = Settings(
            timezone=raw["timezone"],
            grace_days=int(raw["grace_days"]),
            machine=Machine(**raw["machine"]),
            wsl=raw.get("wsl", "all"),
            auto_capture_minutes=int(raw.get("auto_capture_minutes", DEFAULT_AUTO_CAPTURE_MINUTES)),
        )
    else:
        timezone = codexbar_timezone() or system_timezone()
        settings = Settings(timezone=timezone, grace_days=DEFAULT_GRACE_DAYS, machine=_new_machine())
        paths.data.mkdir(parents=True, exist_ok=True)
        paths.settings.write_text(json.dumps(asdict(settings), indent=2) + "\n")
    ZoneInfo(settings.timezone)  # fail fast on an invalid timezone
    if settings.grace_days < 1:
        raise ValueError(f"grace_days must be >= 1, got {settings.grace_days}")
    if settings.auto_capture_minutes != 0 and settings.auto_capture_minutes < 10:
        raise ValueError(f"auto_capture_minutes must be 0 (off) or at least 10, got {settings.auto_capture_minutes}")
    if settings.wsl not in ("all", "running", "off"):
        raise ValueError(f"wsl must be all, running or off, got {settings.wsl!r}")
    return settings


def save_settings(paths: Paths, settings: Settings) -> None:
    ZoneInfo(settings.timezone)
    paths.data.mkdir(parents=True, exist_ok=True)
    tmp = paths.settings.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(settings), indent=2) + "\n")
    tmp.replace(paths.settings)


def default_paths() -> Paths:
    return Paths(data=data_dir())
