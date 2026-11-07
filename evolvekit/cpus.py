"""The machine's physical cores, and how a tuning run should use them.

A time-limited solver is scored on what it gets done within its time, so two
runs sharing a core each score worse than one run alone, and the difference
becomes the score. A plan therefore runs one worker per *physical* core,
pinned to one logical CPU of that core, and leaves the first core to the
operating system and to evolvekit itself: on a four-core, eight-thread laptop
that is three workers on logical CPUs 2, 4 and 6.

The layout comes from the operating system -- `GetLogicalProcessorInformationEx`
on Windows, sysfs on Linux -- and falls back to "every logical CPU is a core"
where neither answers. Standard library only.
"""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

from evolvekit.evaluate.process import can_pin

__all__ = ["physical_cores", "plan_workers"]

_SYSFS = Path("/sys/devices/system/cpu")


def physical_cores() -> list[list[int]]:
    """Logical CPU ids grouped by physical core, the core with CPU 0 first."""
    layout = None
    if sys.platform == "win32":
        layout = _windows()
    elif sys.platform.startswith("linux"):
        layout = _linux()
    if hasattr(os, "sched_getaffinity") and layout:
        # In a container or a cpuset a process may use fewer CPUs than exist.
        allowed = os.sched_getaffinity(0)
        layout = [[cpu for cpu in core if cpu in allowed] for core in layout]
        layout = [core for core in layout if core]
    if not layout:
        layout = [[cpu] for cpu in range(os.cpu_count() or 1)]
    return sorted((sorted(core) for core in layout), key=min)


def plan_workers(runs: int, cores: list[list[int]] | None = None) -> tuple[int, list[int]]:
    """`(workers, pin_cpus)` for a stage with `runs` independent runs: one
    worker per physical core but the first (at least one, at most `runs`),
    each pinned to the first logical CPU of its core. No pins where this
    platform cannot pin, or when there is only one core."""
    layout = cores if cores is not None else physical_cores()
    workers = max(1, min(len(layout) - 1, runs))
    if not can_pin() or len(layout) < 2:
        return workers, []
    return workers, [core[0] for core in layout[1:]][:workers]


def _linux() -> list[list[int]] | None:
    cores: dict[tuple[int, int], list[int]] = {}
    for entry in _SYSFS.glob("cpu[0-9]*"):
        name = entry.name[3:]
        if not name.isdigit():
            continue
        try:
            package = int((entry / "topology" / "physical_package_id").read_text().strip())
            core = int((entry / "topology" / "core_id").read_text().strip())
        except (OSError, ValueError):
            continue
        cores.setdefault((package, core), []).append(int(name))
    return sorted((sorted(cpus) for cpus in cores.values()), key=min) or None


def _windows() -> list[list[int]] | None:
    """`GetLogicalProcessorInformationEx(RelationProcessorCore)`, parsed by hand:
    one PROCESSOR_RELATIONSHIP per core, each with the affinity masks of its
    logical processors."""
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        query = kernel32.GetLogicalProcessorInformationEx
        query.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
        query.restype = wintypes.BOOL
        length = wintypes.DWORD(0)
        query(0, None, ctypes.byref(length))  # 0 = RelationProcessorCore; asks for the size
        if not length.value:
            return None
        buffer = ctypes.create_string_buffer(length.value)
        if not query(0, buffer, ctypes.byref(length)):
            return None
        raw = buffer.raw[: length.value]
    except (OSError, AttributeError, ValueError):
        return None
    mask_size = struct.calcsize("P")
    affinity_size = mask_size + 8  # KAFFINITY Mask; WORD Group; WORD Reserved[3]
    cores: list[list[int]] = []
    offset = 0
    while offset + 8 <= len(raw):
        relationship, size = struct.unpack_from("<II", raw, offset)
        if size <= 0:
            break
        if relationship == 0:
            # PROCESSOR_RELATIONSHIP after Relationship and Size: Flags,
            # EfficiencyClass, Reserved[20], GroupCount at +22, GroupMask[] at +24.
            (group_count,) = struct.unpack_from("<H", raw, offset + 8 + 22)
            logical: list[int] = []
            for index in range(group_count):
                base = offset + 8 + 24 + index * affinity_size
                mask = int.from_bytes(raw[base : base + mask_size], "little")
                (group,) = struct.unpack_from("<H", raw, base + mask_size)
                logical += [group * 64 + bit for bit in range(mask_size * 8) if mask >> bit & 1]
            if logical:
                cores.append(sorted(logical))
        offset += size
    return sorted(cores, key=min) or None
