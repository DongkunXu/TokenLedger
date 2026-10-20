"""Encrypted, authenticated sync messages.

Every request and response body is gzip-compressed JSON sealed with AES-256-GCM
under the ledger's encryption key. The associated data binds each message to
its direction, path, sending machine, time and request id, so a sealed body
cannot be replayed as another request, and a response cannot be passed off as
the answer to a different request. Anyone without the key can neither read nor
forge either side; in particular a fake hub cannot acknowledge a push, which is
what would make a machine believe its data had arrived.
"""

from __future__ import annotations

import gzip
import json
import os
import secrets
import time

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PROTOCOL = 1
MAX_SKEW_S = 600  # requests older or newer than this (by the hub's clock) are refused
MAX_BODY = 64 * 1024 * 1024

H_MACHINE = "X-TokenLedger-Machine"
H_TIME = "X-TokenLedger-Time"
H_REQUEST = "X-TokenLedger-Request"
CONTENT_TYPE = "application/x-tokenledger-sealed"


class EnvelopeError(ValueError):
    """The message was not sealed with this key, was altered, or does not belong here."""


def _aad(direction: str, path: str, machine: str, stamp: str, request_id: str) -> bytes:
    return f"tokenledger-sync/{PROTOCOL}\n{direction}\n{path}\n{machine}\n{stamp}\n{request_id}".encode()


def seal(key: bytes, payload: dict, aad: bytes) -> bytes:
    nonce = os.urandom(12)
    body = gzip.compress(json.dumps(payload, separators=(",", ":")).encode(), compresslevel=6)
    return nonce + AESGCM(key).encrypt(nonce, body, aad)


def unseal(key: bytes, blob: bytes, aad: bytes) -> dict:
    if len(blob) < 12 + 16:
        raise EnvelopeError("message too short")
    try:
        body = AESGCM(key).decrypt(blob[:12], blob[12:], aad)
    except InvalidTag as exc:
        raise EnvelopeError("message was not sealed with this ledger key, or was altered") from exc
    try:
        return json.loads(gzip.decompress(body))
    except (OSError, ValueError) as exc:
        raise EnvelopeError("sealed message is not valid JSON") from exc


def new_request(key: bytes, path: str, machine: str, payload: dict, *, now: float | None = None) -> tuple[dict, bytes]:
    """Headers and body for one request."""
    stamp = str(int(now if now is not None else time.time()))
    request_id = secrets.token_hex(12)
    headers = {H_MACHINE: machine, H_TIME: stamp, H_REQUEST: request_id, "Content-Type": CONTENT_TYPE}
    return headers, seal(key, payload, _aad("request", path, machine, stamp, request_id))


def open_request(key: bytes, path: str, headers, body: bytes, *, now: float | None = None) -> tuple[str, str, dict]:
    """Check and decrypt a request. Returns (machine, request_id, payload)."""
    machine, stamp, request_id = headers.get(H_MACHINE), headers.get(H_TIME), headers.get(H_REQUEST)
    if not machine or not stamp or not request_id:
        raise EnvelopeError("missing sync headers")
    if len(body) > MAX_BODY:
        raise EnvelopeError("message too large")
    payload = unseal(key, body, _aad("request", path, machine, stamp, request_id))
    try:
        skew = abs((now if now is not None else time.time()) - int(stamp))
    except ValueError as exc:
        raise EnvelopeError("bad time header") from exc
    if skew > MAX_SKEW_S:
        raise ClockSkewError(skew)
    return machine, request_id, payload


class ClockSkewError(EnvelopeError):
    def __init__(self, skew: float) -> None:
        super().__init__(f"clocks differ by {skew / 60:.0f} minutes; set both machines to the right time")
        self.skew = skew


def seal_response(key: bytes, path: str, machine: str, request_id: str, payload: dict) -> bytes:
    return seal(key, payload, _aad("response", path, machine, "", request_id))


def open_response(key: bytes, path: str, machine: str, request_id: str, body: bytes) -> dict:
    return unseal(key, body, _aad("response", path, machine, "", request_id))
