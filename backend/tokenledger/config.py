"""Paths and persistent settings.

Settings live in ``<data>/settings.json`` and are created on first use. The
timezone decides how days are bucketed; it must match CodexBar's own
``tokenCostUsageBucketTimeZone`` so that a day means the same thing on both
sides. The machine id is generated once and identifies this computer's series.
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

DEFAULT_TIMEZONE = "Europe/London"
DEFAULT_GRACE_DAYS = 3


def project_root() -> Path:
    env = os.environ.get("TOKENLEDGER_ROOT")
    if env:
        return Path(env)
    # backend/tokenledger/config.py -> project root is three levels up
    return Path(__file__).resolve().parents[2]


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

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


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
        )
    else:
        settings = Settings(timezone=DEFAULT_TIMEZONE, grace_days=DEFAULT_GRACE_DAYS, machine=_new_machine())
        paths.data.mkdir(parents=True, exist_ok=True)
        paths.settings.write_text(json.dumps(asdict(settings), indent=2) + "\n")
    ZoneInfo(settings.timezone)  # fail fast on an invalid timezone
    if settings.grace_days < 1:
        raise ValueError(f"grace_days must be >= 1, got {settings.grace_days}")
    return settings


def default_paths() -> Paths:
    return Paths(data=data_dir())
