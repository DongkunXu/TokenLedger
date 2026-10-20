"""The models.dev price catalog, in the form CodexBar keeps it.

On a Mac, CodexBar maintains ``~/Library/Caches/CodexBar/model-pricing/models-dev-v1.json``
and TokenLedger reads it as it is. CodexBar's Linux CLI keeps the same file under
``~/.cache``. Where neither exists (Windows, or Linux without CodexBar),
TokenLedger keeps its own copy in ``<data>/cache/models-dev-v1.json``, built the
way CodexBar builds it (``ModelsDevPricing.swift``), so prices work out the same:

* fetched from https://models.dev/api.json, refreshed when older than 24 hours
* each provider keeps ``id``, ``name`` and ``models``; each model ``id``, ``name``,
  ``cost`` (input, output, cache_read, cache_write, context_over_200k, tiers) and
  ``limit.context``; a model with a malformed field is dropped
* a fetch without priced Anthropic and OpenAI models is not trusted; the old file stays
* models that models.dev no longer lists stay in the file (CodexBar's fallback merge),
  so history keeps its prices
* the file is ``{"version": 1, "fetchedAt": "...Z", "catalog": {"providers": ...}}``

When the fetch fails, an older copy is used. Copying a Mac's CodexBar file into
``<data>/cache/`` gives exactly that Mac's prices.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import data_dir

URL = "https://models.dev/api.json"
FILE_NAME = "models-dev-v1.json"
MAX_AGE = timedelta(hours=24)
COST_KEYS = ("input", "output", "cache_read", "cache_write")


class CatalogError(RuntimeError):
    pass


def codexbar_paths() -> list[Path]:
    home = Path.home()
    paths = [home / "Library/Caches/CodexBar/model-pricing" / FILE_NAME]
    xdg = os.environ.get("XDG_CACHE_HOME")
    paths.append((Path(xdg) if xdg else home / ".cache") / "CodexBar/model-pricing" / FILE_NAME)
    return paths if sys.platform != "win32" else []


def own_path() -> Path:
    return data_dir() / "cache" / FILE_NAME


def catalog_bytes(*, fetch: bool = True, now: datetime | None = None) -> bytes:
    """The catalog file to price with: CodexBar's when it has one, else TokenLedger's own,
    refreshed from models.dev when it is older than a day."""
    for path in codexbar_paths():
        if path.is_file():
            return path.read_bytes()
    path = own_path()
    current = path.read_bytes() if path.is_file() else None
    now = now or datetime.now(timezone.utc)
    if fetch and (current is None or _age(current, now) > MAX_AGE):
        try:
            refreshed = refresh(path, current, now=now)
        except CatalogError:
            if current is None:
                raise
            refreshed = None
        if refreshed is not None:
            return refreshed
    if current is None:
        raise CatalogError(f"no price catalog: CodexBar's is missing and {path} does not exist")
    return current


def refresh(path: Path, current: bytes | None, *, now: datetime) -> bytes | None:
    """Fetch models.dev and write the catalog file. Returns the new file, or None when the
    fetch did not look trustworthy (the old file stays)."""
    try:
        with urllib.request.urlopen(urllib.request.Request(URL, headers={"User-Agent": "TokenLedger"}), timeout=20) as resp:
            api = json.loads(resp.read())
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise CatalogError(f"could not fetch {URL}: {exc}") from exc
    old = json.loads(current) if current else None
    built = build(api, old, now)
    if built is None:
        return None
    data = json.dumps(built, separators=(",", ":")).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    return data


def _age(raw: bytes, now: datetime) -> timedelta:
    try:
        fetched = datetime.fromisoformat(json.loads(raw)["fetchedAt"].replace("Z", "+00:00"))
    except (ValueError, KeyError, TypeError):
        return timedelta.max
    return now - fetched


# -- building the file like CodexBar -----------------------------------------------------


def build(api: dict, old: dict | None, fetched_at: datetime) -> dict | None:
    providers = decode(api)
    if not plausible(providers):
        return None
    if old is not None:
        providers = merge_fallback(providers, old.get("catalog", {}).get("providers", {}))
    return {
        "version": 1,
        "fetchedAt": fetched_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "catalog": {"providers": providers},
    }


def _num(v) -> bool:
    return v is None or (isinstance(v, (int, float)) and not isinstance(v, bool))


def _rates(d, *, with_tier: bool) -> dict:
    if not isinstance(d, dict):
        raise ValueError
    out = {}
    for k in COST_KEYS:
        if not _num(d.get(k)):
            raise ValueError
        if d.get(k) is not None:
            out[k] = d[k]
    if with_tier and d.get("tier") is not None:
        t = d["tier"]
        size = t.get("size") if isinstance(t, dict) else None
        if not (isinstance(t, dict) and isinstance(t.get("type"), str) and isinstance(size, int) and not isinstance(size, bool)):
            raise ValueError
        out["tier"] = {"type": t["type"], "size": size}
    return out


def decode_model(m) -> dict:
    if not isinstance(m, dict) or not isinstance(m.get("id"), str):
        raise ValueError
    out: dict = {"id": m["id"]}
    if m.get("name") is not None:
        if not isinstance(m["name"], str):
            raise ValueError
        out["name"] = m["name"]
    cost = m.get("cost")
    if cost is not None:
        c = _rates(cost, with_tier=False)
        if cost.get("context_over_200k") is not None:
            c["context_over_200k"] = _rates(cost["context_over_200k"], with_tier=True)
        if cost.get("tiers") is not None:
            if not isinstance(cost["tiers"], list):
                raise ValueError
            c["tiers"] = [_rates(t, with_tier=True) for t in cost["tiers"]]
        out["cost"] = c
    limit = m.get("limit")
    if limit is not None:
        if not isinstance(limit, dict):
            raise ValueError
        ctx = limit.get("context")
        if ctx is not None and (not isinstance(ctx, int) or isinstance(ctx, bool)):
            raise ValueError
        out["limit"] = {"context": ctx} if ctx is not None else {}
    return out


def decode(api: dict) -> dict:
    providers: dict = {}
    for key, p in api.items():
        if not isinstance(p, dict) or not isinstance(p.get("models"), dict):
            continue
        if any(p.get(f) is not None and not isinstance(p[f], str) for f in ("id", "name")):
            continue
        models = {}
        for mk, m in p["models"].items():
            try:
                models[mk] = decode_model(m)
            except ValueError:
                pass
        provider = {k: p[k] for k in ("id", "name") if p.get(k) is not None}
        provider["models"] = models
        providers[(p.get("id") or key).strip().lower()] = provider
    return providers


def priceable(m: dict) -> bool:
    c = m.get("cost") or {}
    return c.get("input") is not None and c.get("output") is not None


def plausible(providers: dict) -> bool:
    return all(
        any(priceable(m) for m in (providers.get(p) or {}).get("models", {}).values()) for p in ("anthropic", "openai")
    )


def _candidates_keeping_dates(raw: str) -> list[str]:
    out: list[str] = []

    def add(v: str) -> None:
        v = v.strip()
        if v and v not in out:
            out.append(v)

    t = raw.strip()
    add(t)
    if t.startswith("openai/"):
        add(t[len("openai/") :])
    if t.startswith("anthropic."):
        add(t[len("anthropic.") :])
    if "." in t and "claude-" in t:
        tail = t[t.rindex(".") + 1 :]
        if tail.startswith("claude-"):
            add(tail)
    i = 0
    while i < len(out):
        c = out[i]
        if "@" in c:
            base, suffix = c.split("@", 1)
            if re.fullmatch(r"\d{8}", suffix):
                add(f"{base}-{suffix}")
            add(base)
        elif c.startswith("claude-"):
            add(c + "@default")
        m = re.search(r"-v\d+:\d+$", c)
        if m:
            add(c[: m.start()])
        i += 1
    return out


def stable_identity(raw: str) -> str:
    n = raw.strip()

    def canonical(x: str) -> str:
        for c in reversed(_candidates_keeping_dates(x)):
            c = c[: -len("@default")] if c.endswith("@default") else c
            if c:
                return c
        return x.strip()

    if "@" in n:
        base, suffix = n.split("@", 1)
        if re.fullmatch(r"\d{8}", suffix):
            return f"{canonical(base)}-{suffix}"
    return canonical(n)


def merge_fallback(new: dict, old: dict) -> dict:
    merged = {k: {**v, "models": dict(v["models"])} for k, v in new.items()}
    for pid, old_p in old.items():
        pid = pid.strip().lower()
        if pid not in merged:
            merged[pid] = old_p
            continue
        provider = merged[pid]
        counts: dict[str, int] = {}
        for m in provider["models"].values():
            if priceable(m):
                ident = stable_identity(m["id"])
                counts[ident] = counts.get(ident, 0) + 1
        for mk, om in old_p.get("models", {}).items():
            if not priceable(om):
                continue
            ident = stable_identity(om["id"])
            if counts.get(ident, 0):
                continue
            key = mk if mk not in provider["models"] else f"codexbar-fallback:{mk}:{om['id'].strip()}"
            replaced = provider["models"].get(key)
            provider["models"][key] = om
            if replaced is not None and priceable(replaced):
                counts[stable_identity(replaced["id"])] -= 1
            counts[ident] = counts.get(ident, 0) + 1
    return merged
