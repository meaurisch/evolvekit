"""Start a process that outlives the one that starts it.

Through a launcher that exits at once, so the process is nobody's child -- a
host that kills its caller's process tree (a terminal, an IDE) does not reach
it -- detached from any console, in a process group (or session) of its own,
and outside the caller's job object where Windows allows that. A study's run
starts this way, and so does the app that `study open` starts.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

__all__ = ["DetachError", "spawn_detached"]

_DETACHED = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
_BREAKAWAY = 0x01000000  # CREATE_BREAKAWAY_FROM_JOB
LAUNCH_TIMEOUT_S = 60

_LAUNCHER = r"""
import json, os, subprocess, sys
argv, cwd, log = json.loads(sys.argv[1]), sys.argv[2] or None, sys.argv[3] or os.devnull
with open(log, "ab") as out:
    options = dict(cwd=cwd, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, close_fds=True)
    if os.name == "nt":
        try:
            process = subprocess.Popen(argv, creationflags=%d, **options)
        except OSError:
            process = subprocess.Popen(argv, creationflags=%d, **options)
    else:
        process = subprocess.Popen(argv, start_new_session=True, **options)
print(process.pid)
""" % (_DETACHED | _BREAKAWAY, _DETACHED)


class DetachError(OSError):
    """The launcher printed no pid: nothing was started. The message is the
    launcher's last line on stderr."""


def spawn_detached(argv: list[str], *, cwd: Path | None = None, env: dict | None = None,
                   log: Path | None = None) -> int:
    """Run the launcher to its end; the pid of the detached process it
    started. Its output goes to `log` (appended), else nowhere. Raises
    `DetachError` when nothing was started, and lets
    `subprocess.TimeoutExpired` through: a launcher that timed out may have
    started the process."""
    done = subprocess.run([sys.executable, "-c", _LAUNCHER, json.dumps(list(argv)), str(cwd) if cwd else "",
                           str(log) if log else ""],
                          env=env, capture_output=True, text=True, timeout=LAUNCH_TIMEOUT_S)
    try:
        return int(done.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        said = (done.stderr.strip().splitlines() or ["no message"])[-1]
        raise DetachError(said) from None
