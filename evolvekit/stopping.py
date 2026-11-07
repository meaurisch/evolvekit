"""Stopping a run on time, or on request -- and saying which.

`budget.max_hours` is the run's *active* time across sessions: a run resumed
the next morning counts the hours it had already used, not the night in
between (`active_seconds`: per session, from its first event to its last).
Two checks enforce it:

* **Before each generation**, the run does not start one that would not
  finish in time. How long one takes is projected from the longest of the
  last two; with only the seed done, from the seed's duration times the
  number of children -- an upper bound when a cheaper stage screens first.
* **During a generation**, a watcher thread looks every second. When the
  limit is reached, the run's stop event is set: evaluations in flight are
  stopped and counted as *abandoned* -- not failed, nothing went wrong -- and
  evaluations not yet started are not started. The generation is not
  recorded; its children stay in `pending.json`, and the resumed run
  finishes it first, as after any interruption.

A file named `stop-request` in the run directory has the same effect, with
the reason "stopped on request". It is how a program that started the run as
a detached process stops it -- on Windows such a process cannot be sent
Ctrl+C reliably -- and `python -m evolvekit stop` writes it. The run removes
the request once it has acted on it, so the next session does not stop at
once.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

__all__ = ["STOP_REQUEST", "WATCH_INTERVAL_S", "AnyEvent", "RunStop", "active_seconds"]

STOP_REQUEST = "stop-request"
"""The file in a run directory that asks the run to stop."""

WATCH_INTERVAL_S = 1.0
"""How often the watcher looks at the clock and for a request. An evaluation
that is stopped is killed within `evaluate/process.CANCEL_POLL_S` after that."""


def _parse_ts(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def active_seconds(events: Iterable[Mapping[str, Any]], *, exclude_session: str | None = None) -> float:
    """Seconds a run has actually been running, all sessions added up: each
    from its first event to its last. A session that died is counted up to
    the last thing it managed to write -- less than it ran, never more."""
    first: dict[str, datetime] = {}
    last: dict[str, datetime] = {}
    for event in events:
        session, moment = str(event.get("session")), _parse_ts(event.get("ts"))
        if moment is None or session == exclude_session:
            continue
        first.setdefault(session, moment)
        last[session] = moment
    return sum(max(0.0, (last[session] - first[session]).total_seconds()) for session in first)


class AnyEvent:
    """Set as soon as any of its events is. A run in flight can be called off
    by the stage that started it (Ctrl+C lands on the waiting thread) or by
    the run's own stop -- one thing for the process runner to watch."""

    def __init__(self, *events: Any) -> None:
        self._events = [event for event in events if event is not None]

    def is_set(self) -> bool:
        return any(event.is_set() for event in self._events)


def _hours(value: float) -> str:
    return f"{value:g} h"


class RunStop:
    """Whether the run should stop before its plan is done, and why.

    `event` is what every evaluation of the run watches; `reason` is the
    stop reason the run ends with.
    """

    def __init__(
        self,
        run_dir: str | Path,
        *,
        max_hours: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        interval: float = WATCH_INTERVAL_S,
    ) -> None:
        self.run_dir = Path(run_dir)
        self.max_hours = max_hours
        self.max_s = None if max_hours is None else float(max_hours) * 3600.0
        self.used_s = 0.0
        self.event = threading.Event()
        self.reason: str | None = None
        self._clock = clock
        self._began = clock()
        self._interval = float(interval)
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def request_path(self) -> Path:
        return self.run_dir / STOP_REQUEST

    @property
    def triggered(self) -> bool:
        return self.event.is_set()

    def start(self, used_s: float = 0.0, *, watch: bool = True) -> None:
        """Start this session's clock, `used_s` seconds into the run's hours,
        and (with `watch`) the thread that notices a limit or a request."""
        self.used_s = float(used_s)
        self._began = self._clock()
        if watch and self._thread is None:
            self._thread = threading.Thread(target=self._watch, name="evolvekit-stop", daemon=True)
            self._thread.start()

    def elapsed_s(self) -> float:
        """The run's active seconds so far, earlier sessions included."""
        return self.used_s + max(0.0, self._clock() - self._began)

    def trigger(self, reason: str) -> None:
        with self._lock:
            if self.reason is None:
                self.reason = reason
        self.event.set()

    def poll(self) -> str | None:
        """Look at the request file and the clock now. The reason to stop, or
        `None` -- and when there is one, every evaluation is told."""
        if self.event.is_set():
            return self.reason
        if self.request_path.exists():
            self.trigger("stopped on request")
        elif self.max_s is not None and self.elapsed_s() >= self.max_s:
            self.trigger(
                f"time limit reached: {self.elapsed_s() / 3600:.2f} h of active run time, "
                f"budget.max_hours is {_hours(self.max_hours)}"
            )
        return self.reason if self.event.is_set() else None

    def before_generation(self, projected_s: float | None) -> str | None:
        """Why not to start another generation, or `None`. `projected_s` is how
        long one may take; one that would end past the limit is not started."""
        reason = self.poll()
        if reason is not None:
            return reason
        if self.max_s is None or projected_s is None:
            return None
        elapsed = self.elapsed_s()
        if elapsed + projected_s > self.max_s:
            return (
                f"time limit: another round would not finish within {_hours(self.max_hours)} "
                f"({elapsed / 3600:.2f} h used; a round has taken up to {projected_s / 3600:.2f} h)"
            )
        return None

    def _watch(self) -> None:
        while not self._done.wait(self._interval):
            if self.poll() is not None:
                return

    def close(self) -> None:
        """Stop watching. A request the run acted on is removed; one that
        arrived after the run had stopped for another reason is left for the
        next session."""
        self._done.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, 2 * self._interval))
            self._thread = None
        if self.reason == "stopped on request":
            try:
                self.request_path.unlink()
            except OSError:
                pass
