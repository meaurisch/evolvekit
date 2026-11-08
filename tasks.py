"""Uniform entry points: python tasks.py test|full|run|check|lint|dist.

`test` is the fast loop for local iteration: everything except tests marked
`slow` (driver-level runs and a couple of timing-sensitive ones), which was
~70-170s depending on load -- too slow to run after every edit. `full` runs
every test, unfiltered. `lint` runs ruff's mistake-only rules. `check` is
`lint` and then `full`, and it is what CI calls, so it must never lose
coverage `test` skips.

`run` drives the bin-packing example for a few generations against the `fake`
provider: no network, no keys, a few seconds, and a leaderboard at the end.

`dist` builds the Windows app with PyInstaller (the `dist` extra): the folder
`dist/evolvekit-app/`, its zip `dist/evolvekit-app-<version>-win64.zip`, and the
PyVRP harness's zip beside it (packaging/README.md). It is never part of
`check`.
"""

import importlib.metadata
import re
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EXAMPLE = ROOT / "examples" / "binpacking" / "evolvekit.yaml"
RUN_DIR = ROOT / "runs" / "demo"


def _pytest(*extra_args):
    rc = subprocess.call(
        [sys.executable, "-m", "pytest", "-q", *extra_args], cwd=str(ROOT)
    )
    # pytest exit 5 = no tests collected; only tolerate it before tests exist.
    return 0 if rc == 5 and not (ROOT / "tests").is_dir() else rc


def test():
    return _pytest("-m", "not slow")


def full():
    return _pytest()


def run():
    if RUN_DIR.is_dir():
        # The ledger is append-only, so the demo starts from a clean directory.
        import shutil

        shutil.rmtree(RUN_DIR)
    return subprocess.call(
        [
            sys.executable,
            "-m",
            "evolvekit",
            "run",
            "--config",
            str(EXAMPLE),
            "--run-dir",
            str(RUN_DIR),
        ],
        cwd=str(ROOT),
    )


def lint():
    """Undefined names, unused imports and variables, syntax errors: the rules
    that only ever find mistakes. Style is not checked, on purpose -- adopting a
    formatter is a decision for the whole repository, not for a drive-by."""
    paths = [p for p in ("evolvekit", "tests", "examples", "benchmarks", "harnesses", "tasks.py") if (ROOT / p).exists()]
    return subprocess.call(
        [sys.executable, "-m", "ruff", "check", "--select", "F,E9", *paths], cwd=str(ROOT)
    )


def check():
    """The CI gate: lint, then every test. The tests run even when lint fails,
    so one push reports both; either failing fails the gate."""
    linted = lint()
    tested = full()
    return linted or tested


def _pyproject() -> dict:
    with open(ROOT / "pyproject.toml", "rb") as handle:
        return tomllib.load(handle)


def project_version() -> str:
    """`[project].version` of pyproject.toml."""
    return _pyproject()["project"]["version"]


def zip_name(version: str) -> str:
    return f"evolvekit-app-{version}-win64.zip"


def pinned_pyinstaller() -> str | None:
    """The `X.Y.Z` of `pyinstaller==X.Y.Z` in the `dist` extra."""
    for requirement in _pyproject()["project"].get("optional-dependencies", {}).get("dist", []):
        match = re.fullmatch(r"\s*pyinstaller\s*==\s*([0-9][0-9A-Za-z.]*)\s*", requirement, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def _pyinstaller_version() -> str | None:
    try:
        return importlib.metadata.version("pyinstaller")
    except importlib.metadata.PackageNotFoundError:
        return None


def _megabytes(size: int) -> float:
    return size / (1024 * 1024)


def dist():
    """The Windows app: PyInstaller's one-folder build, zipped, and the PyVRP
    harness's zip beside it (it is not bundled)."""
    if sys.platform != "win32":
        print("error: dist builds the Windows app; on macOS and Linux install with  pipx install evolvekit",
              file=sys.stderr)
        return 2
    install = '.venv\\Scripts\\python.exe -m pip install -e ".[dist]"'
    found, pinned = _pyinstaller_version(), pinned_pyinstaller()
    if found is None:
        print(f"error: the dist extra is not installed: {install}", file=sys.stderr)
        return 2
    if found != pinned:
        print(f"error: PyInstaller {found} is installed, the dist extra pins {pinned}: {install}", file=sys.stderr)
        return 2
    out = ROOT / "dist"
    folder = out / "evolvekit-app"
    if folder.exists():
        try:
            shutil.rmtree(folder)
        except PermissionError:
            print("error: dist\\evolvekit-app is in use -- an evolvekit-app.exe or evolvekit-cli.exe from it is "
                  "still running; stop it (evolvekit-cli.exe app stop) and build again", file=sys.stderr)
            return 1
    built = subprocess.call(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", str(out),
         "--workpath", str(ROOT / "build" / "pyinstaller"), str(ROOT / "packaging" / "evolvekit-app.spec")],
        cwd=str(ROOT),
    )
    if built:
        return built
    target = out / zip_name(project_version())
    target.unlink(missing_ok=True)
    unpacked = 0
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                unpacked += path.stat().st_size
                archive.write(path, f"evolvekit-app/{path.relative_to(folder).as_posix()}")
    print(f"wrote {target}: {_megabytes(target.stat().st_size):.1f} MB ({_megabytes(unpacked):.0f} MB unpacked)",
          flush=True)  # before the pack's own line
    for old in out.glob("pyvrp-*.zip"):
        old.unlink()
    return subprocess.call(
        [sys.executable, "-m", "evolvekit", "harness", "pack", str(ROOT / "harnesses" / "pyvrp"), "--out", str(out)],
        cwd=str(ROOT),
    )


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    sys.exit({"test": test, "full": full, "run": run, "check": check, "lint": lint, "dist": dist}[cmd]())
