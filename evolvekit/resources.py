"""Where evolvekit's package data and built-in harnesses live.

"Frozen" means running from a PyInstaller one-folder bundle: `sys.frozen` is
set, `sys._MEIPASS` is `<folder>/_internal` (where the bundled data sits) and
`sys.executable` is the exe that runs (`evolvekit-app.exe` or
`evolvekit-cli.exe`, side by side in `<folder>`).

Every lookup of package data, and every "evolvekit's Python", goes through
this module, so a frozen process and a checkout find the same things.
Standard library only; it imports nothing from evolvekit.
"""

from __future__ import annotations

import sys
from pathlib import Path

__all__ = ["APP_EXE", "CLI_EXE", "bundle_dir", "builtin_harnesses", "is_frozen", "package_root"]

_EXT = ".exe" if sys.platform == "win32" else ""
APP_EXE = "evolvekit-app" + _EXT
CLI_EXE = "evolvekit-cli" + _EXT


def is_frozen() -> bool:
    """True inside a PyInstaller bundle (read on every call)."""
    return bool(getattr(sys, "frozen", False))


def _meipass() -> Path:
    return Path(sys._MEIPASS)  # type: ignore[attr-defined]


def package_root() -> Path:
    """The `evolvekit` package folder: the checkout's, or the bundle's."""
    if is_frozen():
        return _meipass() / "evolvekit"
    return Path(__file__).resolve().parent


def builtin_harnesses() -> Path:
    """The folder of built-in harnesses (a checkout's `harnesses/`, absent in
    an installed wheel; the bundle's when frozen)."""
    if is_frozen():
        return _meipass() / "harnesses"
    return package_root().parent / "harnesses"


def bundle_dir() -> Path | None:
    """The folder holding both exes when frozen (not resolved), else None."""
    return Path(sys.executable).parent if is_frozen() else None
