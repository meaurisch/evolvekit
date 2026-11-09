"""What the app's bundle holds besides evolvekit's code: read by the spec
(`evolvekit-app.spec`) and by the suite (`tests/test_dist_task.py`).

Standard library only. It is loaded by its path, never imported as
`packaging.bundle_files`: `packaging/` has no `__init__.py` and must never get
one, or `import packaging` would find this folder instead of the PyPI library
PyInstaller and pip need.
"""

from __future__ import annotations

import importlib.util
import pkgutil
import subprocess
import sys
import tomllib
from pathlib import Path

__all__ = ["BUILT_INS", "EXAMPLES", "EXCLUDED", "data_files", "stdlib_modules"]

BUILT_INS = ("demo-tour", "prompt-tune")
"""The built-in harnesses the bundle carries. PyVRP is not one: it needs the
planner's own Python with PyVRP, and ships as its own zip (`tasks.py dist`)."""

EXAMPLES = {"cli-solver": ("solver.py", "README.md"), "prompt-pipeline": ("pipeline.py", "prompts", "items")}
"""The built-ins' applications (their `code.repo: ../../examples/<name>`): the
files and folders of `examples/<name>/` the bundle carries, a folder whole."""

EXCLUDED = {"antigravity", "ensurepip", "idlelib", "lib2to3", "pydoc_data", "test", "this", "turtle",
            "turtledemo", "venv"}
"""Standard modules left out of the bundle: jokes, IDLE, turtle graphics, the
installers of pip and venvs, and the standard library's own tests."""

_TEST_PARTS = {"test", "tests", "idle_test"}


def _ignored(relative: Path) -> bool:
    """`pack_harness`'s rule: no caches, no bytecode."""
    return any(part in ("__pycache__", ".pytest_cache") for part in relative.parts) or relative.suffix == ".pyc"


def tracked_files(root: Path) -> set[Path]:
    """Every file of the checkout at `root` that git tracks (`git ls-files`),
    as it is on disk: only these are bundled, so nothing untracked on the
    builder's disk (a `.env`, a scratch run, a local output) ever ships."""
    try:
        done = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, timeout=120)
    except OSError as exc:
        raise RuntimeError(f"the bundle is built from a git checkout, and git cannot run: {exc}") from None
    if done.returncode != 0:
        said = done.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(f"the bundle is built from a git checkout, and {root} is not one: {said}")
    names = done.stdout.decode("utf-8").split("\0")
    return {(root / name).resolve() for name in names if name and (root / name).is_file()}


def _tree(folder: Path, dest: str, tracked: set[Path]) -> list[tuple[str, str]]:
    """Every tracked file under `folder` (the ignore rule applied) as `(source, dest/<its folder>)`."""
    files = []
    for path in sorted(folder.rglob("*")):
        relative = path.relative_to(folder)
        if path.is_file() and path.resolve() in tracked and not _ignored(relative):
            files.append((str(path), Path(dest, relative.parent).as_posix()))
    return files


def data_files(root: Path) -> list[tuple[str, str]]:
    """`(source file, destination folder relative to the bundle's _internal)`:
    evolvekit's package data (the `[tool.setuptools.package-data].evolvekit`
    globs of `root/pyproject.toml`, so the wheel and the bundle ship one list),
    the built-in harnesses and their applications -- of each, only the files
    git tracks (`tracked_files`)."""
    root = Path(root)
    with open(root / "pyproject.toml", "rb") as handle:
        globs = tomllib.load(handle)["tool"]["setuptools"]["package-data"]["evolvekit"]
    tracked = tracked_files(root)
    package = root / "evolvekit"
    files: dict[str, str] = {}
    for pattern in globs:
        for path in sorted(package.glob(pattern)):
            if path.is_file() and path.resolve() in tracked:
                files[str(path)] = Path("evolvekit", path.relative_to(package).parent).as_posix()
    for harness in BUILT_INS:
        files.update(_tree(root / "harnesses" / harness, f"harnesses/{harness}", tracked))
    for name, entries in EXAMPLES.items():
        for entry in entries:
            path = root / "examples" / name / entry
            if path.is_dir():
                files.update(_tree(path, f"examples/{name}/{entry}", tracked))
            elif path.is_file() and path.resolve() in tracked:
                files[str(path)] = f"examples/{name}"
            else:
                raise FileNotFoundError(f"the bundle needs {path}, tracked by git")
    return sorted(files.items())


def stdlib_modules() -> list[str]:
    """Every standard module this interpreter has (less `EXCLUDED` and test
    packages), packages with their submodules: a runner may import any of
    them, and PyInstaller bundles only what it sees imported."""
    names = set()
    for name in sys.stdlib_module_names:
        if name.startswith("_") or name in EXCLUDED:
            continue
        try:
            spec = importlib.util.find_spec(name)
        except (ImportError, ValueError):
            spec = None
        if spec is None:
            continue
        names.add(name)
        if spec.submodule_search_locations:
            for module in pkgutil.walk_packages(spec.submodule_search_locations, prefix=name + ".",
                                                onerror=lambda _: None):
                if not _TEST_PARTS & set(module.name.split(".")):
                    names.add(module.name)
    return sorted(names)
