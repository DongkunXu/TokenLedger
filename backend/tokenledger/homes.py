"""Where Claude Code and Codex keep their data on this computer.

One computer can have several places, and every one of them must be read, once:

* Claude Code: ``CLAUDE_CONFIG_DIR``, ``~/.config/claude`` and ``~/.claude``. The
  terminal CLI, the IDE extensions and the desktop app's Code tab write there; the
  desktop app's agent sessions and claude-swap profiles have their own folders
  (``claude_log_roots``).
* Codex: ``CODEX_HOME``, else ``~/.codex``. The CLI, the IDE extension and the
  desktop app share it.
* Windows: every WSL distribution has its own Linux home folders, with their own
  ``.claude`` and ``.codex``. They are read through ``\\\\wsl.localhost\\<distro>``.
  Reading a distribution that is not running starts it, so ``settings.json`` can
  limit this with ``"wsl": "running"`` or turn it off with ``"wsl": "off"``.

Folders reached by two paths (a symlink, or the same folder named twice) are read
once; the sources also deduplicate requests by their ids, so a session copied
from one folder into another is counted once as well.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

WSL_MODES = ("all", "running", "off")


def _unique(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    out: list[Path] = []
    for p in paths:
        try:
            key = os.path.normcase(str(p.resolve()))
        except OSError:
            key = os.path.normcase(str(p))
        if key not in seen and p.is_dir():
            seen.add(key)
            out.append(p)
    return out


def claude_roots(*, home: Path | None = None, env: dict | None = None, wsl: str = "all") -> list[Path]:
    """Every Claude config folder on this computer that has a ``projects`` folder."""
    env = os.environ if env is None else env
    home = home or Path.home()
    candidates = [Path(env["CLAUDE_CONFIG_DIR"]).expanduser()] if env.get("CLAUDE_CONFIG_DIR", "").strip() else []
    candidates += [home / ".config" / "claude", home / ".claude"]
    for linux_home in wsl_homes(wsl):
        candidates += [linux_home / ".config" / "claude", linux_home / ".claude"]
    return [p for p in _unique(candidates) if (p / "projects").is_dir()]


def claude_log_roots(*, home: Path | None = None, env: dict | None = None, wsl: str = "all") -> list[Path]:
    """Every folder (or single file) holding Claude Code session logs, as CodexBar finds them,
    plus the places CodexBar does not look (Windows, WSL):

    * ``projects`` in every Claude config folder (``claude_roots``)
    * claude-swap profiles: ``~/.claude-swap-backup/sessions/<n>-<label>/projects`` and, on
      Linux, ``$XDG_DATA_HOME/claude-swap/sessions/...``
    * the Claude desktop app's local agent sessions: ``.claude/projects`` folders up to four
      levels below ``local-agent-mode-sessions`` and ``claude-code-sessions`` in its app data
      folder, and the ``audit.jsonl`` files (Cowork) found on the way
    """
    env = os.environ if env is None else env
    home = home or Path.home()
    roots = [p / "projects" for p in claude_roots(home=home, env=env, wsl=wsl)]
    swap_bases = [home / ".claude-swap-backup" / "sessions"]
    if sys.platform.startswith("linux"):
        xdg = env.get("XDG_DATA_HOME")
        swap_bases.append((Path(xdg) if xdg else home / ".local" / "share") / "claude-swap" / "sessions")
    for base in swap_bases:
        try:
            roots += [p / "projects" for p in sorted(base.iterdir()) if re.fullmatch(r"[1-9]\d*-.+", p.name)]
        except OSError:
            pass
    for app in desktop_app_dirs(home=home, env=env):
        for store in ("local-agent-mode-sessions", "claude-code-sessions"):
            roots += _desktop_store(app / store)
    return _unique_paths(roots)


def desktop_app_dirs(*, home: Path, env: dict) -> list[Path]:
    if sys.platform == "darwin":
        return [home / "Library" / "Application Support" / "Claude"]
    if sys.platform == "win32":
        appdata = env.get("APPDATA")
        return [Path(appdata) / "Claude"] if appdata else []
    return []


SKIP_DIRS = {".build", ".git", "build", "DerivedData", "node_modules", "outputs", "target"}


def _desktop_store(base: Path, depth: int = 4) -> list[Path]:
    """``.claude/projects`` folders and ``audit.jsonl`` files up to ``depth`` levels below ``base``."""
    found: list[Path] = []
    level = [base]
    for _ in range(depth + 1):
        next_level: list[Path] = []
        for folder in level:
            try:
                entries = sorted(folder.iterdir())
            except OSError:
                continue
            projects = folder / ".claude" / "projects"
            if projects.is_dir() and not projects.is_symlink():
                found.append(projects)
            for entry in entries:
                if entry.name == "audit.jsonl" and entry.is_file() and not entry.is_symlink():
                    found.append(entry)
                elif entry.is_dir() and not entry.is_symlink() and entry.name not in SKIP_DIRS and not entry.name.startswith("."):
                    next_level.append(entry)
        level = next_level
    return found


def _unique_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    out: list[Path] = []
    for p in paths:
        try:
            key = os.path.normcase(str(p.resolve()))
        except OSError:
            key = os.path.normcase(str(p))
        if key not in seen and p.exists():
            seen.add(key)
            out.append(p)
    return out


def codex_homes(*, home: Path | None = None, env: dict | None = None, wsl: str = "all") -> list[Path]:
    """Every Codex home folder on this computer that exists."""
    env = os.environ if env is None else env
    home = home or Path.home()
    candidates = [Path(env["CODEX_HOME"]).expanduser()] if env.get("CODEX_HOME") else []
    candidates.append(home / ".codex")
    for linux_home in wsl_homes(wsl):
        candidates.append(linux_home / ".codex")
    return _unique(candidates)


def wsl_homes(mode: str = "all") -> list[Path]:
    """Home folders inside the WSL distributions (Windows only; empty elsewhere)."""
    if sys.platform != "win32" or mode == "off":
        return []
    homes: list[Path] = []
    for distro in wsl_distributions(running_only=mode == "running"):
        root = _wsl_root(distro)
        if root is None:
            continue
        try:
            homes += [p for p in (root / "home").iterdir() if p.is_dir()]
        except OSError:
            pass
        homes.append(root / "root")
    return homes


def wsl_distributions(*, running_only: bool = False) -> list[str]:
    """Installed WSL distributions from the registry (this does not start WSL), or the running
    ones from ``wsl.exe --list --running --quiet``."""
    if not running_only:
        names = _registry_distributions()
        if names is not None:
            return names
    args = ["wsl.exe", "--list", "--quiet"] + (["--running"] if running_only else [])
    try:
        out = subprocess.run(args, capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if out.returncode != 0:
        return []
    return parse_wsl_list(out.stdout)


def _registry_distributions() -> list[str] | None:
    try:
        import winreg  # type: ignore[import-not-found]
    except ImportError:
        return None
    names: list[str] = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Lxss") as lxss:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(lxss, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(lxss, sub) as key:
                        name, _ = winreg.QueryValueEx(key, "DistributionName")
                except OSError:
                    continue
                if isinstance(name, str) and name and "docker-desktop" not in name.lower():
                    names.append(name)
    except OSError:
        return []  # WSL was never set up
    return names


def parse_wsl_list(raw: bytes) -> list[str]:
    """wsl.exe writes UTF-16 (little endian) unless WSL_UTF8=1 is set."""
    text = raw.decode("utf-16-le", errors="ignore") if b"\x00" in raw else raw.decode(errors="ignore")
    names = [line.strip().lstrip("﻿").strip() for line in text.splitlines()]
    return [n for n in names if n and "docker-desktop" not in n.lower()]


def _wsl_root(distro: str) -> Path | None:
    for prefix in ("\\\\wsl.localhost\\", "\\\\wsl$\\"):
        root = Path(prefix + distro + "\\")
        try:
            if root.is_dir():
                return root
        except OSError:
            continue
    return None
