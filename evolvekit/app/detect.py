"""Finding the application a harness needs on this machine.

For a `python` harness the application is an interpreter in which the
harness's module imports (`import pyvrp`). People rarely know where theirs is,
so the app looks where interpreters usually are, asks every one it finds --
in parallel, 10 s each -- and lists the matches first:

    PyVRP 0.14.0 found        C:\\work\\.venv-pyvrp\\Scripts\\python.exe
    Python 3.13, no pyvrp     C:\\Users\\me\\AppData\\...\\python.exe

The places: the interpreter running evolvekit; `py -0p` on Windows; `.venv*`
folders in the working directory, the evolvekit checkout and the home
directory; `~/venvs/*`, `~/Envs/*`, `~/.virtualenvs/*`; conda environments;
and the paths used before. A `program` harness has no such places: it offers
the paths used before, and Browse.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Iterable

from evolvekit.harness.library import BUILT_IN
from evolvekit.harness.manifest import Harness
from evolvekit.harness.probe import PROBE_TIMEOUT_S, probe_application

__all__ = ["candidates", "detect"]

_WORKERS = 8


def _python_in(env: Path) -> Path | None:
    for relative in (("Scripts", "python.exe"), ("bin", "python"), ("python.exe",), ("bin", "python3")):
        path = env.joinpath(*relative)
        if path.is_file():
            return path
    return None


def _children(folder: Path) -> list[Path]:
    try:
        return sorted(p for p in folder.iterdir() if p.is_dir())
    except OSError:
        return []


def parse_py_launcher(text: str) -> list[Path]:
    """The paths in `py -0p`'s answer (" -V:3.12 *   C:\\Python312\\python.exe")."""
    found = []
    for line in text.splitlines():
        match = re.search(r"([A-Za-z]:\\.*?\.exe)\s*$", line.strip())
        if match:
            found.append(Path(match.group(1)))
    return found


def _py_launcher() -> list[Path]:
    """The interpreters Windows's `py` launcher knows (`py -0p`)."""
    if sys.platform != "win32":
        return []
    try:
        done = subprocess.run(["py", "-0p"], capture_output=True, text=True, timeout=10, encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return []
    return parse_py_launcher(done.stdout)


def candidates(*, cwd: Path | None = None, recent: Iterable[str] = (), home: Path | None = None) -> list[str]:
    """Every interpreter worth asking, in the order they are listed when
    nothing matches, without duplicates."""
    user = home or Path.home()
    places: list[Path] = [Path(sys.executable)]
    places += _py_launcher()
    for base in (cwd or Path.cwd(), BUILT_IN.parent, user):
        try:
            places += [p for env in sorted(base.glob(".venv*")) if (p := _python_in(env))]
        except OSError:
            continue
    for folder in (user / "venvs", user / "Envs", user / ".virtualenvs"):
        places += [p for env in _children(folder) if (p := _python_in(env))]
    conda_roots = [Path(os.environ["CONDA_PREFIX"])] if os.environ.get("CONDA_PREFIX") else []
    conda_roots += [user / name for name in ("anaconda3", "miniconda3", "miniforge3", "mambaforge")]
    for root in conda_roots:
        if (python := _python_in(root)) is not None:
            places.append(python)
        places += [p for env in _children(root / "envs") if (p := _python_in(env))]
    places += [Path(p) for p in recent]
    seen: set[str] = set()
    listed: list[str] = []
    for place in places:
        key = os.path.normcase(os.path.abspath(place))
        if key in seen or not place.exists():
            continue
        seen.add(key)
        listed.append(str(place))
    return listed


def detect(harness: Harness, *, cwd: Path | None = None, recent: Iterable[str] = (), home: Path | None = None,
           timeout: float = PROBE_TIMEOUT_S) -> list[dict[str, Any]]:
    """Every candidate, asked whether it is the application: matches first."""
    recent = list(recent)
    if harness.application.kind == "program":
        paths = [p for p in recent if Path(p).exists()]
    else:
        paths = candidates(cwd=cwd, recent=recent, home=home)

    def ask(path: str) -> dict[str, Any]:
        probe = probe_application(harness, path, timeout=timeout)
        return {"path": path, "ok": probe.ok, "message": probe.message, "version": probe.version, "python": probe.python}

    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        answers = list(pool.map(ask, paths))
    order = {path: index for index, path in enumerate(paths)}
    return sorted(answers, key=lambda a: (not a["ok"], order[a["path"]]))
