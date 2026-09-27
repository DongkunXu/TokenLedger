"""Compressed archive of every source's untouched output.

Layout: ``<data>/raw/<source>/<YYYY>/<MM>/<YYYYMMDDTHHMMSSZ>-<name>.json.gz``.
The archive lets the ledger be rebuilt or re-parsed later with different rules.
"""

from __future__ import annotations

import gzip
from datetime import datetime
from pathlib import Path


def write(raw_root: Path, source: str, name: str, now_utc: datetime, payload: bytes) -> Path:
    stamp = now_utc.strftime("%Y%m%dT%H%M%SZ")
    folder = raw_root / source / now_utc.strftime("%Y") / now_utc.strftime("%m")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{stamp}-{name}.json.gz"
    n = 1
    while path.exists():  # two captures within the same second
        path = folder / f"{stamp}-{name}-{n}.json.gz"
        n += 1
    tmp = path.with_suffix(".tmp")
    with gzip.open(tmp, "wb", compresslevel=9) as fh:
        fh.write(payload)
    tmp.replace(path)
    return path


def read(path: Path) -> bytes:
    with gzip.open(path, "rb") as fh:
        return fh.read()
