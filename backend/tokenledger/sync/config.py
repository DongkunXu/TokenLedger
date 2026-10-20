"""Sync settings (``<data>/sync.json``) and the shared ledger key.

Every machine in one ledger group shares a random key, made once on the hub
with ``tokenledger sync hub-init`` and entered on each other machine with
``tokenledger sync join``. The key never travels over the network: it encrypts
and authenticates every sync request and response, and lets a machine check
that a hub answering on the local network really holds the same key.

Roles:

* ``hub``: keeps the combined ledger and accepts pushes from the other machines
* ``node``: captures into its own ledger as before, then pushes it to the hub

No sync.json means the machine works on its own, as before sync existed.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from ..config import Paths

KEY_PREFIX = "tlk1-"
KEY_BYTES = 20
DEFAULT_PORT = 8766
ROLES = ("hub", "node")


class SyncConfigError(ValueError):
    pass


def new_key() -> str:
    """A fresh ledger key, e.g. ``tlk1-ABCD-EFGH-...`` (160 random bits)."""
    text = base64.b32encode(secrets.token_bytes(KEY_BYTES)).decode().rstrip("=")
    return KEY_PREFIX + "-".join(text[i : i + 4] for i in range(0, len(text), 4))


def parse_key(text: str) -> bytes:
    """The key's raw bytes. Accepts any case, with or without dashes and spaces."""
    cleaned = "".join(text.split()).replace("-", "").upper()
    prefix = KEY_PREFIX.rstrip("-").upper()
    if cleaned.startswith(prefix) and len(cleaned) == len(prefix) + 32:  # 20 bytes are 32 base32 characters
        cleaned = cleaned[len(prefix) :]
    try:
        raw = base64.b32decode(cleaned + "=" * (-len(cleaned) % 8))
    except (ValueError, binascii.Error) as exc:
        raise SyncConfigError("not a TokenLedger key (expected tlk1-XXXX-...)") from exc
    if len(raw) != KEY_BYTES:
        raise SyncConfigError("not a TokenLedger key (wrong length)")
    return raw


@dataclass(frozen=True)
class Keys:
    """Keys derived from the ledger key, each for one purpose."""

    encryption: bytes  # AES-256-GCM key for request and response bodies
    proof: bytes  # HMAC key for discovery answers
    key_id: str  # short public fingerprint; tells groups apart without revealing the key

    @classmethod
    def derive(cls, key_text: str) -> "Keys":
        raw = parse_key(key_text)

        def sub(label: str) -> bytes:
            return hmac.new(raw, b"tokenledger/" + label.encode(), hashlib.sha256).digest()

        return cls(encryption=sub("encryption"), proof=sub("proof"), key_id=sub("key-id").hex()[:12])


@dataclass(frozen=True)
class SyncConfig:
    role: str
    key: str
    port: int = DEFAULT_PORT  # hub: TCP and UDP port it listens on; node: the hub's port
    listen_host: str = "0.0.0.0"  # hub only
    networks: tuple[str, ...] = ()  # CIDRs to discover on (node) and accept from (hub); empty = private ranges
    hub_url: str | None = None  # node: fixed address, e.g. http://hub.tailnet:8766 (no discovery)
    hub_machine: str | None = None  # node: the hub's machine id, learned when joining
    hub_label: str | None = None
    hub_last_url: str | None = None  # node: where the hub answered last time
    extra: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.role not in ROLES:
            raise SyncConfigError(f"role must be one of {ROLES}, got {self.role!r}")
        parse_key(self.key)
        for cidr in self.networks:
            try:
                ipaddress.ip_network(cidr, strict=False)
            except ValueError as exc:
                raise SyncConfigError(f"not a network: {cidr!r}") from exc

    @property
    def keys(self) -> Keys:
        return Keys.derive(self.key)

    def with_(self, **changes) -> "SyncConfig":
        return replace(self, **changes)


def config_path(paths: Paths) -> Path:
    return paths.data / "sync.json"


def state_path(paths: Paths) -> Path:
    return paths.data / "sync-state.json"


def load(paths: Paths) -> SyncConfig | None:
    path = config_path(paths)
    if not path.exists():
        return None
    raw = json.loads(path.read_text())
    raw["networks"] = tuple(raw.get("networks") or ())
    known = {f for f in SyncConfig.__dataclass_fields__}
    extra = {k: v for k, v in raw.items() if k not in known}
    return SyncConfig(**{k: v for k, v in raw.items() if k in known and k != "extra"}, extra=extra)


def save(paths: Paths, config: SyncConfig) -> None:
    """Write sync.json readable by this user only (it holds the key)."""
    path = config_path(paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = asdict(config)
    data["networks"] = list(config.networks)
    data.update(data.pop("extra") or {})
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(json.dumps(data, indent=2) + "\n")
    tmp.replace(path)


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
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n")
    tmp.replace(path)
