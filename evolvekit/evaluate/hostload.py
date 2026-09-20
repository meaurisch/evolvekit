"""How busy was the machine while this evaluation ran?

For a solver with a time limit, the machine is part of the measurement. A
build, a test suite, a video call on "another" core still takes from the same
power and thermal budget, and the evaluation gets fewer iterations into its ten
minutes -- a worse score that has nothing to do with the candidate. Pinning
(`pin_cpus`) keeps other work off the solver's cores; it does not keep it from
slowing them down. On the laptop this was written on, a test suite pinned to
the one free core cost three pinned solver runs 15-30 % of their iterations,
and nothing anywhere said so.

So every evaluator run records `host_busy`: the share of *all* logical CPUs
that were busy, with anything, between its start and its end. Read against
what the run's own workers explain -- three single-threaded workers on eight
logical CPUs are 0.375 -- it says whether something else was going on. The
status document counts the evaluations where it was.

Standard library only: `GetSystemTimes` on Windows, `/proc/stat` on Linux.
Elsewhere (macOS) the answer is `None`, and nothing is flagged.
"""

from __future__ import annotations

import sys

__all__ = ["HostLoad", "cpu_times"]


def cpu_times() -> tuple[float, float] | None:
    """`(busy, total)` CPU time summed over all logical CPUs, in arbitrary but
    consistent units; `None` where the platform offers no cheap way to ask."""
    try:
        if sys.platform == "win32":
            return _windows_times()
        if sys.platform.startswith("linux"):
            return _linux_times()
    except (OSError, ValueError, AttributeError):
        return None
    return None


def _linux_times() -> tuple[float, float]:
    with open("/proc/stat", encoding="ascii") as handle:
        fields = handle.readline().split()
    if not fields or fields[0] != "cpu":
        raise ValueError("unexpected /proc/stat")
    values = [float(v) for v in fields[1:]]
    idle = values[3] + (values[4] if len(values) > 4 else 0.0)  # idle + iowait
    total = sum(values[:8])  # guest time is already inside user time
    return total - idle, total


def _windows_times() -> tuple[float, float]:  # pragma: no cover - exercised on Windows only
    import ctypes
    from ctypes import wintypes

    idle, kernel, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
    if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
        raise OSError("GetSystemTimes failed")

    def ticks(ft: "wintypes.FILETIME") -> float:
        return float((ft.dwHighDateTime << 32) | ft.dwLowDateTime)

    total = ticks(kernel) + ticks(user)  # kernel time includes idle time
    return total - ticks(idle), total


class HostLoad:
    """The busy share of the whole machine between `start()` and `stop()`."""

    def __init__(self) -> None:
        self._before: tuple[float, float] | None = None

    def start(self) -> "HostLoad":
        self._before = cpu_times()
        return self

    def stop(self) -> float | None:
        after = cpu_times()
        if self._before is None or after is None:
            return None
        busy, total = after[0] - self._before[0], after[1] - self._before[1]
        if total <= 0:
            return None
        return round(min(1.0, max(0.0, busy / total)), 4)
