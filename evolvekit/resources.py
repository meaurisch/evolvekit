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

import os
import sys
from pathlib import Path

__all__ = ["APP_EXE", "CLI_EXE", "app_argv", "bundle_dir", "builtin_harnesses", "exe_name", "is_frozen",
           "package_root", "python_argv", "python_code_argv", "python_module_argv", "python_script_argv"]

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


def exe_name() -> str:
    """The file name of the program this process runs (`evolvekit-app.exe`,
    `evolvekit-cli.exe`, `python.exe`, ...)."""
    return Path(sys.executable).name


def python_argv() -> list[str]:
    """evolvekit's Python, the interpreter alone: what runs, runners, `{python}`
    and every helper process are started with.

    Unfrozen `sys.executable`, or `python.exe` beside it when this process is
    `pythonw.exe` (the service started at logon): a run's output goes to its
    log, and pythonw has no standard streams to give it. Frozen, the console
    exe beside whichever exe runs (`evolvekit-cli.exe`, which runs python's
    forms through the entry), or `sys.executable` when that file is missing."""
    exe = Path(sys.executable)
    if is_frozen():
        cli = exe.parent / CLI_EXE
        return [str(cli) if cli.is_file() else sys.executable]
    if exe.name.lower() == "pythonw.exe":
        console = exe.with_name("python.exe")
        if console.is_file():
            return [str(console)]
    return [sys.executable]


def python_module_argv(module: str) -> list[str]:
    """`python -m MODULE`, as evolvekit's Python (the same shape frozen)."""
    return [*python_argv(), "-m", module]


def python_script_argv(path: str | os.PathLike) -> list[str]:
    """`python FILE`, as evolvekit's Python; frozen `<cli exe> --script FILE`,
    so a file whatever its name runs as a script."""
    if is_frozen():
        return [*python_argv(), "--script", str(path)]
    return [*python_argv(), str(path)]


def python_code_argv(code: str) -> list[str]:
    """`python -c CODE`, as evolvekit's Python."""
    return [*python_argv(), "-c", code]


def app_argv() -> list[str]:
    """The program that serves the app: `python -m evolvekit app`, or frozen
    the app exe (windowless itself), to which `app`'s options are given."""
    if is_frozen():
        return [str(bundle_dir() / APP_EXE)]  # type: ignore[operator]
    return [*python_module_argv("evolvekit"), "app"]
