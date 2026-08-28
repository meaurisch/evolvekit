"""Uniform entry points: python tasks.py test|full|run|check.

`test` is the fast loop for local iteration: everything except tests marked
`slow` (driver-level runs and a couple of timing-sensitive ones), which was
~70-170s depending on load -- too slow to run after every edit. `full` and
`check` run everything, unfiltered; `check` is what CI
calls, so it must never lose coverage `test` skips.

`run` drives the bin-packing example for a few generations against the `fake`
provider: no network, no keys, a few seconds, and a leaderboard at the end.
"""

import subprocess
import sys
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


def check():
    return full()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    sys.exit({"test": test, "full": full, "run": run, "check": check}[cmd]())
