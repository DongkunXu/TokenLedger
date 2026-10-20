"""This machine's link to a cloud ledger (``<data>/sync.json``) and its sync state.

``tokenledger sync login <url>`` signs this machine in: the person approves it in
the browser, and the cloud gives this machine its own device token. The token is
kept in ``sync.json``, readable by this user only; it is never shown and never
leaves this machine except to the cloud ledger it belongs to. Each machine has its
own token, so one machine can be signed out without touching the others.

No sync.json means the machine works on its own, as before sync existed.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterator

from ..config import Paths


class SyncConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Link:
    url: str  # the cloud ledger, e.g. https://tokens.example.com
    user: str
    device_id: str
    token: str
    machine: str  # the machine id this token was issued for
    linked_at: str

    def __post_init__(self) -> None:
        if not self.url.startswith("https://") and not self.url.startswith("http://127.0.0.1"):
            raise SyncConfigError(f"the cloud ledger must be an https:// address, got {self.url!r}")


def config_path(paths: Paths) -> Path:
    return paths.data / "sync.json"


def state_path(paths: Paths) -> Path:
    return paths.data / "sync-state.json"


def load(paths: Paths) -> Link | None:
    path = config_path(paths)
    if not path.exists():
        return None
    raw = json.loads(path.read_text())
    try:
        return Link(**{k: raw[k] for k in Link.__dataclass_fields__})
    except KeyError as exc:
        raise SyncConfigError(f"{path} is incomplete ({exc}); run `tokenledger sync login` again") from exc


def save(paths: Paths, link: Link) -> None:
    """Write sync.json readable by this user only (it holds the device token)."""
    _write_private(config_path(paths), json.dumps(asdict(link), indent=2) + "\n")


def remove(paths: Paths) -> bool:
    removed = False
    for path in (config_path(paths), state_path(paths)):
        if path.exists():
            path.unlink()
            removed = True
    return removed


def load_state(paths: Paths) -> dict:
    try:
        return json.loads(state_path(paths).read_text())
    except (FileNotFoundError, ValueError):
        return {}


def save_state(paths: Paths, state: dict) -> None:
    path = state_path(paths)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n")
    tmp.replace(path)


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    tmp.replace(path)


@contextmanager
def exclusive(paths: Paths, *, wait: bool) -> Iterator[bool]:
    """Hold this ledger's sync lock (a capture, the dashboard and the command line may all
    sync). Yields False without waiting when another process holds it and ``wait`` is off."""
    path = paths.data / "sync.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b") as fh:
        if sys.platform == "win32":
            import msvcrt

            mode = msvcrt.LK_LOCK if wait else msvcrt.LK_NBLCK
            try:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), mode, 1)
            except OSError:
                yield False
                return
            try:
                yield True
            finally:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX if wait else fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
                return
            try:
                yield True
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def host_fingerprint() -> str:
    """A hash of this computer's own hardware or installation id, so a device token copied
    to another computer (with a copied data folder) is refused there. Empty when the
    system offers no such id."""
    ident = ""
    try:
        if sys.platform == "darwin":
            out = subprocess.run(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"], capture_output=True, text=True, timeout=10)
            for line in out.stdout.splitlines():
                if "IOPlatformUUID" in line:
                    ident = line.split("=", 1)[1].strip().strip('"')
        elif sys.platform == "win32":
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as key:
                ident = str(winreg.QueryValueEx(key, "MachineGuid")[0])
        else:
            for candidate in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
                if os.path.isfile(candidate):
                    ident = Path(candidate).read_text().strip()
                    break
    except (OSError, subprocess.SubprocessError):
        ident = ""
    if not ident:
        return ""
    return hashlib.sha256(f"tokenledger/host|{ident}".encode()).hexdigest()[:32]
