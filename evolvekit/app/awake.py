"""Keep the computer awake through a scheduled session (D46).

The scheduler's tick writes `<run>/campaign-span.json` (`{"until_real": iso}`) when it launches a
session of a campaign whose study keeps `keep_awake` on: the real moment the session's window ends.
The run's own process (`evolvekit.app.job`) runs inside `keeping_awake(run_dir)`, which asks the
system not to sleep while that moment has not passed -- and through a final check that runs past it,
since a window's end never stops a final check (D4), so it must not sleep either -- and gives the
request back when the run ends. A session started by hand has no file and keeps nothing awake.

On Windows the request is `SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)`, which holds
only while the thread that made it lives: one daemon thread makes every call. Elsewhere nothing is
asked (a no-op).
"""

from __future__ import annotations

import json
import sys
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

__all__ = ["POLL_S", "keeping_awake", "set_state", "wanted"]

POLL_S = 30.0
"""How often the run looks at its span file and its phase."""
SPAN = "campaign-span.json"
"""`scheduler.SPAN`, named here too: the run's process need not import the scheduler."""
LAUNCH = "launch.json"
"""`jobs.LAUNCH`: its `launch` is the run's latest session."""
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


def _read(path: Path) -> dict[str, Any] | None:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def wanted(run_dir: Path, *, real_now: datetime) -> bool:
    """Whether the run should keep the computer awake now: its span file exists and `real_now` is
    before its `until_real`, or it exists and the run (`job.json`) is in its final check. A file
    written for another session (its `session` is not `launch.json`'s `launch`) counts as none: a
    later session by hand of the same run keeps nothing awake."""
    span = _read(Path(run_dir) / SPAN)
    if span is None:
        return False
    session = span.get("session")
    launch = (_read(Path(run_dir) / LAUNCH) or {}).get("launch")
    if isinstance(session, int) and isinstance(launch, int) and session != launch:
        return False
    if (_read(Path(run_dir) / "job.json") or {}).get("phase") == "check":
        return True
    try:
        until = datetime.fromisoformat(str(span.get("until_real")))
    except ValueError:
        return False
    if until.tzinfo is None:
        until = until.astimezone()
    now = real_now if real_now.tzinfo is not None else real_now.astimezone()
    return now.timestamp() < until.timestamp()


def _kernel32() -> Any:
    import ctypes

    return ctypes.windll.kernel32  # type: ignore[attr-defined]


def set_state(on: bool) -> None:
    """Ask the system not to sleep (`on`), or give that request back; nothing off Windows. Call it
    from one thread: the request holds only while the thread that made it lives."""
    if sys.platform != "win32":
        return
    _kernel32().SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED if on else ES_CONTINUOUS)


@contextmanager
def keeping_awake(run_dir: Path, *, set_state: Callable[[bool], None] = set_state,
                  poll_s: float = POLL_S) -> Iterator[None]:
    """The block runs with the computer kept awake while `wanted(run_dir)`: one daemon thread looks
    every `poll_s` and calls `set_state` whenever the answer changes, and
    `set_state(False)` when the block ends, if it had asked. A fault in it never ends the run."""
    done = threading.Event()

    def watch() -> None:
        on = False
        try:
            while True:
                want = wanted(run_dir, real_now=datetime.now(timezone.utc))
                if want != on:
                    set_state(want)
                    on = want
                if done.wait(poll_s):
                    break
        except Exception:  # noqa: BLE001 - keeping awake is a courtesy: the run goes on without it
            pass
        finally:
            if on:
                try:
                    set_state(False)
                except Exception:  # noqa: BLE001
                    pass

    thread = threading.Thread(target=watch, name="evolvekit-keep-awake", daemon=True)
    thread.start()
    try:
        yield
    finally:
        done.set()
        thread.join(timeout=5)
