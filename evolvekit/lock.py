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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

__all__ = ["RunLock", "RunLockError", "lock_owner_alive", "pid_alive", "pid_started_at", "run_lock"]


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


def pid_started_at(pid: int) -> datetime | None:
    """When the process now holding `pid` started, in UTC -- or `None` where
    that cannot be read (macOS, a process we may not query, no such pid).

    Pids are recycled, quickly on Windows and always across a reboot. A
    process that started after a lock was written cannot be that lock's owner,
    whatever its pid."""
    try:
        if sys.platform == "win32":
            return _started_windows(pid)
        if sys.platform.startswith("linux"):
            return _started_linux(pid)
    except (OSError, ValueError, AttributeError):
        return None
    return None


def _started_windows(pid: int) -> datetime | None:
    import ctypes

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        created, other = ctypes.c_ulonglong(0), ctypes.c_ulonglong(0)
        ok = kernel32.GetProcessTimes(
            handle, ctypes.byref(created), ctypes.byref(other), ctypes.byref(other), ctypes.byref(other)
        )
        if not ok or not created.value:
            return None
        # FILETIME: 100 ns ticks since 1601-01-01 UTC.
        return datetime(1601, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=created.value // 10)
    finally:
        kernel32.CloseHandle(handle)


def _started_linux(pid: int) -> datetime | None:
    stat = Path(f"/proc/{pid}/stat").read_text()
    ticks = int(stat.rsplit(")", 1)[1].split()[19])  # field 22, counted after the command name
    boot = next(
        int(line.split()[1]) for line in Path("/proc/stat").read_text().splitlines() if line.startswith("btime ")
    )
    return datetime.fromtimestamp(boot + ticks / os.sysconf("SC_CLK_TCK"), tz=timezone.utc)


def lock_owner_alive(lock: dict[str, Any]) -> bool:
    """Whether the process named by a `.lock` payload is still its owner:
    alive, and -- where its start time can be read -- not younger than the lock."""
    pid = int(lock.get("pid") or 0)
    if not pid_alive(pid):
        return False
    started = lock.get("started")
    if not isinstance(started, str):
        return True
    try:
        locked_at = datetime.fromisoformat(started)
    except ValueError:
        return True
    if locked_at.tzinfo is None:
        locked_at = locked_at.replace(tzinfo=timezone.utc)
    born = pid_started_at(pid)
    # The owner started before it wrote the lock; `started` has whole seconds.
    return born is None or born <= locked_at + timedelta(seconds=2)


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
            if pid != os.getpid() and lock_owner_alive(existing):
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
