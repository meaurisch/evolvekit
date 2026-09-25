"""One external command, under a wall-clock bound that covers its whole tree.

`subprocess.run(timeout=...)` was not that bound. An evaluator is rarely a
single process -- it is a wrapper that starts a solver, or a script that
re-launches itself under another interpreter -- and `run` kills the direct
child only. With pipes attached it then waits for every process still holding
the other end, so:

* a 2-second timeout on a wrapper whose solver hung returned after the solver
  did, a minute later, and the solver was never killed at all;
* an evaluation that had *finished* was held until its timeout because a
  process it had started still owned stdout, and was then reported as a
  timeout.

Two changes make the bound real. The command's output goes to files rather
than pipes, so nothing here ever waits on a handle somebody else holds -- and
the full text of every evaluation stays on disk instead of being cut to a
prompt-sized tail. And the command's whole tree is destroyed when the command
returns, times out, or the wait is interrupted.

An evaluation is over when its command returns. Whatever it left running is
killed too: on a time-limited objective a leftover solver shares a core with
the next candidate and silently becomes that candidate's score.

How the tree is held together differs by platform; see `_PosixTree` and
`_WindowsTree`. Both are best effort against a process that sets out to
escape, and exact for the ordinary case of a wrapper and its solver.

All of that holds while evolvekit is alive to do it. When evolvekit itself is
hard-killed -- a crash, `taskkill /F`, `kill -9`, the session it ran in going
away -- only what the operating system does on its own still happens: on
Windows the Job Object takes down every process that stayed inside it, and
nothing else. A process that had left the job (a packaged Python does, see
`_WindowsTree`) and every process on POSIX outlives it, and nothing reclaims
them when the run is resumed: after a hard kill, look for leftover solvers
before resuming.
"""

from __future__ import annotations

import functools
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

__all__ = ["BoundedRun", "run_bounded", "read_tail", "can_pin"]

REAP_TIMEOUT_S = 10.0
"""How long to wait for a killed tree to be reaped before moving on. The kill
is not a request, so this only ever runs out on a process stuck in the kernel."""

TAIL_BYTES = 64 * 1024

CANCEL_POLL_S = 0.2
"""How often a run that can be called off looks whether it has been."""


@dataclass(frozen=True)
class BoundedRun:
    """What happened to one command."""

    returncode: int | None
    """`None` when the command timed out or could not be started."""
    timed_out: bool
    duration_s: float
    error: str | None = None
    """Why the command could not be started at all (`OSError`), else `None`."""
    cancelled: bool = False
    """Called off through `cancel` before it finished; its tree is gone."""


def run_bounded(
    argv: Sequence[str],
    *,
    timeout: float,
    cwd: str | Path,
    stdout_path: Path,
    stderr_path: Path,
    cpus: Sequence[int] = (),
    cancel: threading.Event | None = None,
) -> BoundedRun:
    """Run `argv`; never take longer than `timeout` plus the time to kill it.

    `cpus` pins the whole tree to those logical CPUs -- for a time-limited
    solver, the difference between a run that had a core and one that shared
    it. `cancel`, once set, ends the run as a timeout would: several runs in
    flight on worker threads have no Ctrl+C of their own to be reached by.
    """
    started = time.perf_counter()
    deadline = started + timeout
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    with open(stdout_path, "wb") as out, open(stderr_path, "wb") as err:
        try:
            tree = _spawn(
                list(argv),
                tuple(cpus),
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=err,
                cwd=str(cwd),
            )
        except OSError as exc:
            return BoundedRun(None, False, time.perf_counter() - started, str(exc))

        returncode: int | None = None
        timed_out = False
        cancelled = False
        try:
            while True:
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    timed_out = True
                    break
                if cancel is not None and cancel.is_set():
                    cancelled = True
                    break
                patience = min(remaining, tree.watch_interval())
                if cancel is not None:
                    patience = min(patience, CANCEL_POLL_S)
                try:
                    returncode = tree.proc.wait(timeout=patience)
                    break
                except subprocess.TimeoutExpired:
                    tree.watch()
        finally:
            # Finished, timed out, or interrupted by Ctrl+C: the tree goes.
            tree.kill()
            try:
                tree.proc.wait(timeout=REAP_TIMEOUT_S)
            except subprocess.TimeoutExpired:  # pragma: no cover - kernel-stuck
                pass
            tree.close()
    return BoundedRun(
        returncode, timed_out, time.perf_counter() - started, cancelled=cancelled
    )


def read_tail(path: Path, limit: int) -> str:
    """The last `limit` characters of a log file, decoded leniently.

    Reads at most `TAIL_BYTES` from the end, so an evaluator that prints fifty
    megabytes costs fifty megabytes of disk and not of memory.
    """
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - TAIL_BYTES))
            data = handle.read()
    except OSError:
        return ""
    text = data.decode("utf-8", errors="replace").replace("\r\n", "\n")
    return text[-limit:] if limit > 0 else ""


def _spawn(
    argv: list[str], cpus: tuple[int, ...] = (), **popen_kwargs: Any
) -> "_PosixTree | _WindowsTree":
    """Start `argv` as the root of a tree. Raises `OSError` exactly as `Popen`."""
    if sys.platform == "win32":
        return _WindowsTree.spawn(argv, popen_kwargs, cpus)
    return _PosixTree.spawn(argv, popen_kwargs, cpus)


def can_pin() -> bool:
    """Whether this platform lets a process be pinned to CPUs at all."""
    return sys.platform == "win32" or hasattr(os, "sched_setaffinity")


# --------------------------------------------------------------------------
# POSIX: a session
# --------------------------------------------------------------------------


class _PosixTree:
    """The command in a session of its own, hence a process group of its own:
    `killpg` reaches every descendant that has not deliberately left it."""

    def __init__(self, proc: subprocess.Popen) -> None:
        self.proc = proc

    @classmethod
    def spawn(
        cls, argv: list[str], popen_kwargs: dict[str, Any], cpus: tuple[int, ...] = ()
    ) -> "_PosixTree":
        if not cpus or not hasattr(os, "sched_setaffinity"):  # macOS has no such call
            return cls(subprocess.Popen(argv, start_new_session=True, **popen_kwargs))
        # An affinity mask is per thread and inherited across fork, so the
        # calling thread wears the child's mask for the length of the spawn.
        # That pins the child from its first instruction -- setting it on the
        # pid afterwards would miss any thread it had already started -- and
        # needs no `preexec_fn`, which is not safe in a threaded parent.
        before = os.sched_getaffinity(0)
        try:
            os.sched_setaffinity(0, set(cpus))
        except OSError:  # a CPU outside this process's cpuset: run unpinned
            return cls(subprocess.Popen(argv, start_new_session=True, **popen_kwargs))
        try:
            return cls(subprocess.Popen(argv, start_new_session=True, **popen_kwargs))
        finally:
            os.sched_setaffinity(0, before)

    def watch_interval(self) -> float:
        return float("inf")  # nothing to watch: the group is the container

    def watch(self) -> None:
        return None

    def kill(self) -> None:
        try:
            os.killpg(self.proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        if self.proc.poll() is None:  # a group we could not signal: at least this
            self.proc.kill()

    def close(self) -> None:
        return None


# --------------------------------------------------------------------------
# Windows: a Job Object, and the parent links while they can be trusted
# --------------------------------------------------------------------------
#
# Windows has no process groups to kill, and `taskkill /T` walks parent links
# that are gone the moment a wrapper exits before its solver. Two mechanisms
# cover each other's gaps:
#
# * A **Job Object** with kill-on-close. It does not care who started whom, and
#   the operating system closes it if evolvekit itself is killed, so a
#   hard-killed run takes with it every process that is still in the job --
#   which is not every process, see below. A job only contains what is
#   started *after* the assignment, and a virtual environment's `python.exe` is
#   a shim that starts the real interpreter within a millisecond -- so the
#   command is created suspended, put in the job, and only then resumed.
#
# * **Watching the parent links.** A packaged (Microsoft Store) Python breaks
#   away from its parent's job, taking everything below it along; on such a
#   machine the job holds the shim and nothing else. So while the command runs
#   the process table is sampled, and every process whose parent is already
#   known is adopted and *pinned with an open handle*. Windows does not recycle
#   a process id while a handle to it is open, so a parent link into a pinned
#   process is genuine even after that process has exited -- which is what
#   makes it safe to kill by it. A creation-time check rules out the one
#   remaining impostor: an older orphan whose dead parent's id this tree
#   happened to be given. Watching is done by evolvekit, so it ends with
#   evolvekit: a hard-killed run leaves a broken-away tree running. Measured
#   on Windows 11 with a Store Python venv: a Python evaluator died with the
#   run, the Python solver it had started did not; a native tree (`cmd /c`
#   and what it ran) stayed in the job and died with it.
#
# Sampling is dense while the tree is young, when launchers come and go, and
# once a second after that: about four milliseconds per sample, on the core
# that is only waiting anyway. A process that lives for less than a sampling
# interval and leaves a child behind can still slip through; that is the
# stated limit.

_WATCH_SCHEDULE = ((0.25, 0.01), (2.0, 0.05))
"""`(younger than, sample every)` in seconds; older trees fall through to
`_WATCH_STEADY_INTERVAL_S`. A shell or a shim is gone within milliseconds of
starting what it wraps, an interpreter takes tens of them to start anything."""
_WATCH_STEADY_INTERVAL_S = 1.0

_CREATE_SUSPENDED = 0x00000004
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_TH32CS_SNAPPROCESS = 0x00000002
_PROCESS_TERMINATE = 0x0001
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_SYNCHRONIZE = 0x00100000


class _WindowsTree:  # pragma: no cover - exercised on Windows only
    def __init__(self, proc: subprocess.Popen, job: Any | None) -> None:
        self.proc = proc
        self._job = job
        self._started = time.perf_counter()
        # pid -> (handle, creation time). The root's handle is `Popen`'s own.
        root = int(proc._handle)  # type: ignore[attr-defined]
        self._pinned: dict[int, tuple[int, int]] = {proc.pid: (root, _created(root))}

    @classmethod
    def spawn(
        cls, argv: list[str], popen_kwargs: dict[str, Any], cpus: tuple[int, ...] = ()
    ) -> "_WindowsTree":
        proc = subprocess.Popen(argv, creationflags=_CREATE_SUSPENDED, **popen_kwargs)
        job = _open_job(proc)
        # While it is still suspended: a process's mask is inherited by every
        # process it starts, so the whole tree is pinned from its first instruction.
        _pin(proc, cpus)
        if not _resume(proc):
            # A process that cannot be resumed can never finish. Start it the
            # plain way rather than make evaluation impossible; watching the
            # parent links still covers what the job then misses.
            proc.kill()
            proc.wait()
            if job is not None:
                _close_handle(job)
            proc = subprocess.Popen(argv, **popen_kwargs)
            job = _open_job(proc)
            _pin(proc, cpus)
        tree = cls(proc, job)
        tree.watch()
        return tree

    def watch_interval(self) -> float:
        age = time.perf_counter() - self._started
        for younger_than, interval in _WATCH_SCHEDULE:
            if age < younger_than:
                return interval
        return _WATCH_STEADY_INTERVAL_S

    def watch(self) -> None:
        """Adopt, and pin, every process whose parent is already pinned."""
        table = _process_table()
        adopted = True
        while adopted:  # a whole chain can appear between two samples
            adopted = False
            for pid, parent in table.items():
                if pid in self._pinned or parent not in self._pinned:
                    continue
                handle = _open_process(pid)
                if not handle:
                    continue
                born = _created(handle)
                if born < self._pinned[parent][1]:
                    _close_handle(handle)  # older than its "parent": not ours
                    continue
                self._pinned[pid] = (handle, born)
                adopted = True

    def kill(self) -> None:
        if self._job is not None:
            _terminate_job(self._job)
        if self.proc.poll() is None:
            self.proc.kill()
        # Whatever broke away from the job. A second round, because a process
        # can start a child between the sample and its own death -- but only a
        # tree that has descendants can grow any.
        for _ in range(2):
            self.watch()
            if len(self._pinned) == 1:
                break
            for pid, (handle, _born) in self._pinned.items():
                if pid != self.proc.pid:
                    _terminate(handle)

    def close(self) -> None:
        for pid, (handle, _born) in self._pinned.items():
            if pid != self.proc.pid:  # the root's handle belongs to `Popen`
                _close_handle(handle)
        self._pinned = {pid: v for pid, v in self._pinned.items() if pid == self.proc.pid}
        if self._job is not None:
            _close_handle(self._job)
            self._job = None


@functools.lru_cache(maxsize=1)
def _kernel32() -> Any:  # pragma: no cover - Windows only
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle, dword, bool_ = wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL
    signatures = {
        "CreateJobObjectW": (handle, [wintypes.LPVOID, wintypes.LPCWSTR]),
        "SetInformationJobObject": (bool_, [handle, ctypes.c_int, wintypes.LPVOID, dword]),
        "AssignProcessToJobObject": (bool_, [handle, handle]),
        "TerminateJobObject": (bool_, [handle, wintypes.UINT]),
        "CloseHandle": (bool_, [handle]),
        "OpenProcess": (handle, [dword, bool_, dword]),
        "TerminateProcess": (bool_, [handle, wintypes.UINT]),
        "SetProcessAffinityMask": (bool_, [handle, ctypes.c_size_t]),
        "GetProcessTimes": (bool_, [handle] + [wintypes.LPVOID] * 4),
        "CreateToolhelp32Snapshot": (handle, [dword, dword]),
        "Process32FirstW": (bool_, [handle, wintypes.LPVOID]),
        "Process32NextW": (bool_, [handle, wintypes.LPVOID]),
    }
    for name, (restype, argtypes) in signatures.items():
        function = getattr(k32, name)
        function.restype, function.argtypes = restype, argtypes
    return k32


def _open_job(proc: subprocess.Popen) -> Any | None:  # pragma: no cover
    """A kill-on-close job holding `proc`, or `None` when one cannot be had
    (a parent job that forbids nesting)."""
    import ctypes
    from ctypes import wintypes

    class _IoCounters(ctypes.Structure):
        _fields_ = [(f"counter{i}", ctypes.c_ulonglong) for i in range(6)]

    class _BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimits),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    try:
        k32 = _kernel32()
        job = k32.CreateJobObjectW(None, None)
        if not job:
            return None
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        configured = k32.SetInformationJobObject(
            job,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        )
        root = int(proc._handle)  # type: ignore[attr-defined]
        if not configured or not k32.AssignProcessToJobObject(job, root):
            k32.CloseHandle(job)
            return None
        return job
    except (OSError, AttributeError, ValueError):
        return None


def _pin(proc: subprocess.Popen, cpus: tuple[int, ...]) -> bool:  # pragma: no cover - Windows only
    """Restrict `proc` (and whatever it starts from now on) to `cpus`."""
    if not cpus:
        return False
    mask = 0
    for cpu in cpus:
        mask |= 1 << cpu
    try:
        return bool(_kernel32().SetProcessAffinityMask(int(proc._handle), mask))  # type: ignore[attr-defined]
    except (OSError, AttributeError, ValueError):
        return False


def _resume(proc: subprocess.Popen) -> bool:  # pragma: no cover - Windows only
    """Resume a process created suspended. `NtResumeProcess` takes the process
    handle `Popen` kept; the documented `ResumeThread` wants the thread handle
    it closed. psutil resumes processes the same way."""
    import ctypes
    from ctypes import wintypes

    try:
        ntdll = ctypes.WinDLL("ntdll")
        ntdll.NtResumeProcess.restype = ctypes.c_long
        ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
        return ntdll.NtResumeProcess(int(proc._handle)) == 0  # type: ignore[attr-defined]
    except (OSError, AttributeError, ValueError):
        return False


def _process_table() -> dict[int, int]:  # pragma: no cover - Windows only
    """`{pid: parent pid}` for every process running right now."""
    import ctypes
    from ctypes import wintypes

    class _ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    table: dict[int, int] = {}
    try:
        k32 = _kernel32()
        snapshot = k32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
        if not snapshot or snapshot == wintypes.HANDLE(-1).value:
            return table
        try:
            entry = _ProcessEntry()
            entry.dwSize = ctypes.sizeof(_ProcessEntry)
            more = k32.Process32FirstW(snapshot, ctypes.byref(entry))
            while more:
                table[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
                more = k32.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            k32.CloseHandle(snapshot)
    except OSError:
        return {}
    return table


def _open_process(pid: int) -> int:  # pragma: no cover - Windows only
    rights = _PROCESS_TERMINATE | _PROCESS_QUERY_LIMITED_INFORMATION | _SYNCHRONIZE
    try:
        return int(_kernel32().OpenProcess(rights, False, pid) or 0)
    except OSError:
        return 0


def _created(handle: int) -> int:  # pragma: no cover - Windows only
    """Creation time in 100 ns ticks; 0 when it cannot be read."""
    import ctypes

    created, other = ctypes.c_ulonglong(0), ctypes.c_ulonglong(0)
    try:
        ok = _kernel32().GetProcessTimes(
            handle,
            ctypes.byref(created),
            ctypes.byref(other),
            ctypes.byref(other),
            ctypes.byref(other),
        )
    except OSError:
        return 0
    return int(created.value) if ok else 0


def _terminate(handle: int) -> None:  # pragma: no cover - Windows only
    try:
        _kernel32().TerminateProcess(handle, 1)
    except OSError:
        pass


def _terminate_job(job: Any) -> None:  # pragma: no cover - Windows only
    try:
        _kernel32().TerminateJobObject(job, 1)
    except OSError:
        pass


def _close_handle(handle: Any) -> None:  # pragma: no cover - Windows only
    try:
        _kernel32().CloseHandle(handle)
    except OSError:
        pass
