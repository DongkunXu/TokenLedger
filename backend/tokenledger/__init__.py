"""TokenLedger: an append-only ledger of AI token usage and estimated cost."""

import functools
import subprocess
from pathlib import Path

__version__ = "0.2.0"


@functools.cache
def build_version() -> str:
    """The version with the git commit this copy runs, e.g. ``0.2.0+d5cf1f0`` (``.modified``
    when files differ from it), so each machine shows exactly which code it runs. The plain
    version when this is no git checkout."""
    root = Path(__file__).resolve().parents[2]
    try:
        commit = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5
        ).stdout.strip()
        if not commit:
            return __version__
        dirty = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"], capture_output=True, text=True, timeout=5
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return __version__
    return f"{__version__}+{commit}{'.modified' if dirty else ''}"
