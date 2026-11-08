"""Start a process that outlives the one that starts it.

Through a launcher that exits at once, so the process is nobody's child -- a
host that kills its caller's process tree (a terminal, an IDE) does not reach
it -- detached from any console, in a process group (or session) of its own,
and outside the caller's job object where Windows allows that. A study's run
starts this way, and so does the app that `study open` starts.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

__all__ = ["DetachError", "no_window_flags", "python_for_runs", "spawn_detached"]

_DETACHED = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
_BREAKAWAY = 0x01000000  # CREATE_BREAKAWAY_FROM_JOB
_NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW
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


def no_window_flags() -> int:
    """`creationflags` for a console program the app or a run starts: `CREATE_NO_WINDOW` on
    Windows, so a process with no console of its own (the service under `pythonw.exe`, a detached
    app or run) never opens a console window for it; 0 elsewhere. Only for a program whose output
    is captured or sent to a file: it gets a hidden console, not the caller's."""
    return _NO_WINDOW if sys.platform == "win32" else 0


def python_for_runs() -> str:
    """The interpreter runs and launchers are started with: `sys.executable`, or `python.exe`
    beside it when this process is `pythonw.exe` (the service started at logon, D16) -- a run's
    output goes to its log, and pythonw has no standard streams to give it."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        console = exe.with_name("python.exe")
        if console.is_file():
            return str(console)
    return sys.executable


class DetachError(OSError):
    """The launcher printed no pid: nothing was started. The message is the
    launcher's last line on stderr."""


def _private_file(path: Path) -> None:
    """Create `path` readable by its owner only (or leave it, made so), for
    the launcher to append to."""
    try:
        os.close(os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600))
        if os.name != "nt":
            os.chmod(path, 0o600)
    except OSError as exc:  # nothing was started: as the launcher would have said
        raise DetachError(f"cannot write {path}: {exc.strerror or exc}") from None


def spawn_detached(argv: list[str], *, cwd: Path | None = None, env: dict | None = None,
                   log: Path | None = None) -> int:
    """Run the launcher to its end; the pid of the detached process it
    started. Its output goes to `log` (appended), else nowhere. Raises
    `DetachError` when nothing was started, and lets
    `subprocess.TimeoutExpired` through: a launcher that timed out may have
    started the process.

    The log is the user's alone (0600 on POSIX): the app's carries its
    address with the key."""
    if log:
        _private_file(Path(log))
    # No console window for the launcher either: the service that starts it has none to share.
    done = subprocess.run([python_for_runs(), "-c", _LAUNCHER, json.dumps(list(argv)), str(cwd) if cwd else "",
                           str(log) if log else ""],
                          env=env, capture_output=True, text=True, timeout=LAUNCH_TIMEOUT_S,
                          creationflags=no_window_flags())
    try:
        return int(done.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        said = (done.stderr.strip().splitlines() or ["no message"])[-1]
        raise DetachError(said) from None
