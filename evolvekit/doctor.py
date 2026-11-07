"""`evolvekit doctor`: is this machine ready, and if not, what to do.

Every check is `{"ok", "name", "message", "fix"}`; `fix` is empty when ok. A
key is never read into a message: only whether it is set.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from urllib.parse import urlsplit

__all__ = ["checks"]

MIN_PYTHON = (3, 11)
_CLI_PROVIDERS = {"claude-cli": ("CLAUDE_CLI_PATH", "claude"), "copilot-cli": ("COPILOT_CLI_PATH", "copilot")}


def _check(ok: bool, name: str, message: str, fix: str = "") -> dict:
    return {"ok": ok, "name": name, "message": message, "fix": "" if ok else fix}


def _python() -> dict:
    version = ".".join(str(n) for n in sys.version_info[:3])
    message = f"{version} at {sys.executable}"
    wanted = ".".join(str(n) for n in MIN_PYTHON)
    return _check(
        sys.version_info[:2] >= MIN_PYTHON, "python", message,
        f"Install Python {wanted} or later from python.org, then install evolvekit with it again.",
    )


def _version() -> dict:
    try:
        from importlib.metadata import version

        found = version("evolvekit")
    except Exception:
        from evolvekit import __version__ as found
    return _check(True, "evolvekit", str(found))


def _home_check(root: Path) -> dict:
    folder = str(root)
    if not root.is_dir():
        return _check(False, "home", f"{folder} does not exist",
                      f"Create the folder {folder}, or choose another with --home or the EVOLVEKIT_HOME variable.")
    writable = os.access(root, os.W_OK)
    return _check(
        writable, "home", f"{folder}, {'writable' if writable else 'not writable'}",
        f"Make the folder {folder} writable for you, or choose another with --home or the EVOLVEKIT_HOME variable.",
    )


def _app(root: Path) -> dict:
    from evolvekit.app.running import read_running

    doc = read_running(root)
    if doc is None:
        return _check(True, "app", "not running (start it with: evolvekit app)")
    parts = urlsplit(str(doc.get("url") or ""))
    # The address in app.json carries the launch's key; show only where it is.
    where = f"{parts.scheme}://{parts.netloc}/" if parts.netloc else f"port {doc.get('port')}"
    return _check(True, "app", f"running at {where}")


def _provider(home) -> dict:
    name = home.settings()["provider"]
    if name in _CLI_PROVIDERS:
        variable, program = _CLI_PROVIDERS[name]
        wanted = os.environ.get(variable) or program
        found = shutil.which(wanted) or (wanted if Path(wanted).is_file() else None)
        if found:
            return _check(True, "provider", f"{name}, program found at {found}")
        return _check(False, "provider", f"{name}, program {wanted!r} not found",
                      f"Install the {program} program, or set {variable} to its full path.")
    from evolvekit.app.store import KEY_NAMES

    keys = home.keys()
    missing = [n for n in KEY_NAMES[name] if not keys.get(n)]
    if missing:
        return _check(False, "provider", f"{name}, no key",
                      f"Set {', '.join(missing)} in the app's settings, or in {home.root / '.env'}.")
    return _check(True, "provider", f"{name}, key set")


def _harnesses(root: Path) -> tuple[dict, list]:
    from evolvekit.harness.library import find_harnesses

    entries = find_harnesses(root)
    loaded = [e for e in entries if e.harness is not None]
    broken = [e for e in entries if e.harness is None]
    if not loaded:
        return _check(False, "harnesses", "none found",
                      "Install one with: evolvekit harness install <file.zip> --home <home>"), []
    message = ", ".join(e.harness.id for e in loaded)
    if broken:
        message += f" ({len(broken)} more could not be read: " + "; ".join(f"{e.path.name}: {e.problem}" for e in broken) + ")"
    return _check(True, "harnesses", message), loaded


def _probes(entries: list, app: str) -> list[dict]:
    from evolvekit.harness.probe import probe_application

    out = []
    for entry in entries:
        probe = probe_application(entry.harness, app)
        out.append(_check(
            probe.ok, f"probe:{entry.harness.id}", probe.message,
            f"Point --app at the application {entry.harness.id} needs.",
        ))
    return out


def checks(home: Path, *, app: str | None = None) -> list[dict]:
    from evolvekit.app.store import Home

    root = Path(home).expanduser().resolve()
    try:
        store = Home(root)  # makes a missing home, as every other command does
    except OSError:
        store = None
    result = [_python(), _version(), _home_check(root), _app(root)]
    if store is None:
        result.append(_check(False, "provider", "unknown, the home cannot be opened",
                             f"Fix the home folder {root} first."))
    else:
        result.append(_provider(store))
    harness_check, loaded = _harnesses(root)
    result.append(harness_check)
    if app:
        result += _probes(loaded, app)
    return result
