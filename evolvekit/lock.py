"""One writer per run directory.

`runs.jsonl` and `usage.jsonl` are append-only, so two runs pointed at the same
directory do not corrupt each other's lines -- they corrupt each other's
*meaning*: interleaved lineage, double-counted spend, an archive rebuilt from
two searches. The lock makes that a refusal instead of a mystery.

`<run_dir>/.lock` holds the owning pid. A lock whose pid is no longer alive is
stale -- a crashed or killed run must not need manual cleanup -- so it is
reclaimed with a note. Liveness is checked without `os.kill` on Windows, where
`os.kill(pid, 0)` calls `TerminateProcess` and would kill the very process it
was asked about.
"""

from __future__ import annotations

import json
import os
import socket
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

__all__ = ["RunLock", "RunLockError", "pid_alive", "run_lock"]


class RunLockError(RuntimeError):
    """Another live process already owns this run directory."""


def pid_alive(pid: int) -> bool:
    """True when a process with this pid exists. Never signals it."""
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if sys.platform == "win32":
        return _pid_alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # exists, owned by somebody else
        return True
    except OSError:
        return True
    return True


def _pid_alive_windows(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True  # it exists; we simply may not ask how it is doing
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


class RunLock:
    """A pid file with an owner check and a stale-owner reclaim."""

    def __init__(self, run_dir: str | Path) -> None:
        self.run_dir = Path(run_dir)
        self.path = self.run_dir / ".lock"
        self.held = False
        self.reclaimed_from: int | None = None

    # -- inspection ------------------------------------------------------

    def read(self) -> dict[str, Any] | None:
        if not self.path.is_file():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {"pid": 0, "note": "unreadable lock file"}
        return payload if isinstance(payload, dict) else {"pid": 0}

    # -- lifecycle -------------------------------------------------------

    def acquire(self) -> "RunLock":
        self.run_dir.mkdir(parents=True, exist_ok=True)
        existing = self.read()
        if existing is not None:
            pid = int(existing.get("pid", 0) or 0)
            if pid_alive(pid) and pid != os.getpid():
                raise RunLockError(
                    f"run directory {self.run_dir} is locked by pid {pid} "
                    f"(started {existing.get('started', 'unknown')}). Point "
                    "--run-dir somewhere else, or wait for that run to finish. "
                    f"If you are sure it is gone, delete {self.path}."
                )
            if pid == os.getpid():
                raise RunLockError(
                    f"run directory {self.run_dir} is already locked by this "
                    f"process (pid {pid}): one run per directory at a time."
                )
            self.reclaimed_from = pid
        payload = {
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        self.path.write_text(
            json.dumps(payload, indent=2), encoding="utf-8", newline="\n"
        )
        self.held = True
        return self

    def release(self) -> None:
        if not self.held:
            return
        current = self.read()
        if current is None or int(current.get("pid", 0) or 0) == os.getpid():
            self.path.unlink(missing_ok=True)
        self.held = False

    def __enter__(self) -> "RunLock":
        return self.acquire()

    def __exit__(self, *_exc: object) -> None:
        self.release()


@contextmanager
def run_lock(run_dir: str | Path) -> Iterator[RunLock]:
    lock = RunLock(run_dir).acquire()
    try:
        yield lock
    finally:
        lock.release()
