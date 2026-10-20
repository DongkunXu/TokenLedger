"""Finding the hub on the local network (UDP broadcast).

A machine looking for its hub broadcasts a short question on the sync port; a
hub answers with its machine id, label and TCP port, plus a proof (an HMAC of
the question's random nonce made with the ledger key). A machine that holds the
key accepts only answers whose proof checks out, so another TokenLedger hub on
the same network (a school or office network can have several) is never used
by mistake. Without a key (before joining) every hub is listed, unverified,
with the short key id that tells groups apart.

Broadcasts reach the local subnet only. Machines on other networks (Tailscale,
a Cloudflare tunnel, another site) set a fixed ``hub_url`` instead.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import secrets
import socket
import threading
import time
from dataclasses import dataclass

from .config import Keys

ASK = b"TLASK1 "
ANSWER = b"TLHUB1 "
PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(n)
    for n in (
        "127.0.0.0/8",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "169.254.0.0/16",
        "100.64.0.0/10",  # carrier-grade NAT, also used by Tailscale
        "::1/128",
        "fc00::/7",
        "fe80::/10",
    )
)


@dataclass(frozen=True)
class HubInfo:
    machine: str
    label: str
    host: str
    port: int
    key_id: str
    version: str
    verified: bool  # the proof matched this machine's key

    @property
    def url(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{host}:{self.port}"


def allowed(address: str, networks: tuple[str, ...]) -> bool:
    """Whether a peer address is inside the configured networks (private ranges when none are set)."""
    try:
        ip = ipaddress.ip_address(address.split("%")[0])
    except ValueError:
        return False
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    if ip.is_loopback:
        return True
    nets = [ipaddress.ip_network(n, strict=False) for n in networks] if networks else PRIVATE_NETWORKS
    return any(ip.version == n.version and ip in n for n in nets)


def proof(keys: Keys, nonce: str, machine: str, port: int) -> str:
    return hmac.new(keys.proof, f"hub|{nonce}|{machine}|{port}".encode(), hashlib.sha256).hexdigest()


def answer_for(question: bytes, *, keys: Keys, machine: str, label: str, port: int, version: str) -> bytes | None:
    """The hub's reply to one question, or None when it should stay silent."""
    if not question.startswith(ASK):
        return None
    try:
        ask = json.loads(question[len(ASK) :])
        nonce = str(ask["nonce"])
    except (ValueError, KeyError, TypeError):
        return None
    if len(nonce) > 64 or (ask.get("key_id") not in (None, keys.key_id)):
        return None
    reply = {
        "machine": machine,
        "label": label,
        "port": port,
        "key_id": keys.key_id,
        "version": version,
        "proof": proof(keys, nonce, machine, port),
    }
    return ANSWER + json.dumps(reply).encode()


class Responder:
    """Answers discovery questions on the hub until stopped (runs in a thread)."""

    def __init__(
        self, *, keys: Keys, machine: str, label: str, port: int, version: str, host: str = "0.0.0.0", networks=()
    ) -> None:
        self.args = dict(keys=keys, machine=machine, label=label, port=port, version=version)
        self.networks = tuple(networks)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((host, port))
        self.sock.settimeout(0.5)
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, name="discovery", daemon=True)

    def start(self) -> "Responder":
        self.thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self.thread.join(timeout=2)
        self.sock.close()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                data, peer = self.sock.recvfrom(2048)
            except (TimeoutError, socket.timeout):
                continue
            except OSError:
                return
            if not allowed(peer[0], self.networks):
                continue
            reply = answer_for(data, **self.args)
            if reply:
                try:
                    self.sock.sendto(reply, peer)
                except OSError:
                    pass


def broadcast_targets(networks: tuple[str, ...]) -> list[str]:
    """Where to send the question: each configured network's broadcast address, else
    the limited broadcast and this machine's /24 (most home and office networks)."""
    targets: list[str] = []
    for cidr in networks:
        net = ipaddress.ip_network(cidr, strict=False)
        if net.version == 4:
            targets.append(str(net.broadcast_address) if net.prefixlen < 32 else str(net.network_address))
    if not networks:
        targets.append("255.255.255.255")
        local = primary_ipv4()
        if local and not local.startswith("127."):
            targets.append(str(ipaddress.ip_network(f"{local}/24", strict=False).broadcast_address))
    return list(dict.fromkeys(targets))


def primary_ipv4() -> str | None:
    """The address this machine uses for outgoing traffic (no packet is sent)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("192.0.2.1", 9))  # TEST-NET-1: never routed, but picks the default interface
            return s.getsockname()[0]
        except OSError:
            return None


def discover(
    *,
    port: int,
    keys: Keys | None = None,
    networks: tuple[str, ...] = (),
    targets: list[str] | None = None,
    timeout: float = 2.0,
) -> list[HubInfo]:
    """Ask the network for hubs. With ``keys``, only hubs of this ledger answer and each is verified."""
    nonce = secrets.token_hex(16)
    question = ASK + json.dumps({"nonce": nonce, "key_id": keys.key_id if keys else None}).encode()
    found: dict[tuple[str, str], HubInfo] = {}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind(("0.0.0.0", 0))
        for target in targets if targets is not None else broadcast_targets(networks):
            try:
                sock.sendto(question, (target, port))
            except OSError:
                continue  # e.g. no route for one of the networks
        deadline = time.monotonic() + timeout
        while (left := deadline - time.monotonic()) > 0:
            sock.settimeout(left)
            try:
                data, peer = sock.recvfrom(2048)
            except (TimeoutError, socket.timeout):
                break
            except OSError:
                continue  # Windows reports an unreachable port as an error on the next receive
            info = _parse_answer(data, peer[0], nonce, keys)
            if info and (not networks or allowed(peer[0], networks)):
                found[(info.machine, info.host)] = info
    return sorted(found.values(), key=lambda h: (not h.verified, h.label, h.host))


def _parse_answer(data: bytes, host: str, nonce: str, keys: Keys | None) -> HubInfo | None:
    if not data.startswith(ANSWER):
        return None
    try:
        a = json.loads(data[len(ANSWER) :])
        machine, port = str(a["machine"]), int(a["port"])
        info = dict(label=str(a["label"]), key_id=str(a["key_id"]), version=str(a.get("version", "")))
        given = str(a["proof"])
    except (ValueError, KeyError, TypeError):
        return None
    verified = False
    if keys is not None:
        if info["key_id"] != keys.key_id or not hmac.compare_digest(given, proof(keys, nonce, machine, port)):
            return None
        verified = True
    return HubInfo(machine=machine, host=host, port=port, verified=verified, **info)
