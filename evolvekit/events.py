"""What a run says about itself while it is running.

`runs.jsonl` is the record of what a run *found*, and it gets a row when a
generation's whole cascade has returned. When one evaluation takes an hour
that is one write every few hours, and in between nothing in the run directory
moves: a run that is working and a run that died at midnight look the same
from outside, and so do "evaluating the third candidate" and "stuck".

Two files close that gap, in the run directory beside the ledger:

    events.jsonl     append-only, one JSON object per line
    heartbeat.json   overwritten every few seconds while the run is alive

`events.jsonl` follows the rule the ledger already lives by -- the log is the
record, every view is a projection of it -- so nothing here is ever rewritten
and a reader needs no protocol beyond "read the lines". Every event carries

    seq       1, 2, 3 ... across the whole directory, sessions included
    ts        UTC, ISO 8601, milliseconds
    session   one id per process that ran against this directory
    pid       that process
    type      what happened; the remaining keys depend on it

The types the driver and the cascade emit: `run_started` (what the run is: the
objective, the stages, the caps, how many generations are planned),
`generation_started`, `candidate_bred`, `eval_started`, `eval_finished` (one
pair per evaluator run: candidate, stage, seed, duration, KPIs or the failure
with its command line and the tail of its stderr), `generation_finished`,
`log` (every line the run printed) and exactly one of `run_finished`,
`run_interrupted` or `run_crashed`. A session whose last event is none of those
three did not get the chance to write one.

`heartbeat.json` is what tells "busy" from "gone": a process that is alive but
suspended, or a machine that went to sleep in the middle of a wall-clock-limited
evaluation, shows up as a beat that is older than its own interval.
"""

from __future__ import annotations

import json
import math
import os
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evolvekit.ledger import _atomic_write, read_jsonl

__all__ = [
    "EVENTS_FILE",
    "HEARTBEAT_FILE",
    "HEARTBEAT_INTERVAL_S",
    "TERMINAL_EVENTS",
    "EventLog",
    "Heartbeat",
    "read_events",
    "read_heartbeat",
    "utc_now",
]

EVENTS_FILE = "events.jsonl"
HEARTBEAT_FILE = "heartbeat.json"

HEARTBEAT_INTERVAL_S = 5.0
"""Short enough that "is it alive?" is answered within seconds, long enough
that a day-long run rewrites one small file seventeen thousand times and not a
million."""

TERMINAL_EVENTS = ("run_finished", "run_interrupted", "run_crashed")
"""How a session can end when it gets the chance to say so."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _jsonable(value: Any) -> Any:
    """`value`, in a shape every JSON reader accepts.

    `json.dumps` writes `NaN` and `Infinity` by default and a browser's
    `JSON.parse` refuses both, so a single stray float from an evaluator would
    blind every consumer of the log. They become `null`. Anything else JSON has
    no word for -- a `Path`, a dataclass -- is written as its text.
    """
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(v) for v in value]
    return str(value)


class EventLog:
    """Appends to `<run_dir>/events.jsonl`. Safe to share between threads."""

    def __init__(self, run_dir: str | Path, *, session: str | None = None) -> None:
        self.run_dir = Path(run_dir)
        self.path = self.run_dir / EVENTS_FILE
        self.session = session or uuid.uuid4().hex[:8]
        self._lock = threading.Lock()
        self._seq: int | None = None
        self._warned = False

    def _open_sequence(self) -> int:
        """Pick the numbering up where the last session left it.

        A process that died mid-write leaves a last line with no newline. The
        next event must not be glued onto that fragment, or it is lost with it.
        """
        self.run_dir.mkdir(parents=True, exist_ok=True)
        last = 0
        for event in read_jsonl(self.path):
            try:
                last = max(last, int(event.get("seq", 0)))
            except (TypeError, ValueError):
                continue
        if self.path.is_file() and self.path.stat().st_size:
            with self.path.open("rb") as handle:
                handle.seek(-1, os.SEEK_END)
                torn = handle.read(1) != b"\n"
            if torn:
                with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write("\n")
        return last

    def emit(self, type: str, **fields: Any) -> dict[str, Any]:
        """Append one event and return it as written.

        An event that cannot be written is lost, not raised: a sync client or a
        virus scanner holding the file for a moment must not end a day-long
        run, nor replace the exception a `run_crashed` event was reporting. The
        numbering goes on regardless, so a gap in `seq` marks the loss; the
        first one is reported on stderr, since the log itself cannot carry it.
        """
        with self._lock:
            event: dict[str, Any] = {
                "seq": None,
                "ts": utc_now(),
                "session": self.session,
                "pid": os.getpid(),
                "type": type,
                **_jsonable(fields),
            }
            try:
                if self._seq is None:
                    self._seq = self._open_sequence()
                self._seq += 1
                event["seq"] = self._seq
                with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(json.dumps(event, allow_nan=False) + "\n")
            except OSError as exc:
                if not self._warned:
                    self._warned = True
                    print(
                        f"evolvekit: could not write {self.path} ({exc}); the run "
                        "goes on, and events are lost until writing works again",
                        file=sys.stderr,
                    )
            return event


def read_events(run_dir: str | Path) -> list[dict[str, Any]]:
    """Every well-formed event in the directory, oldest first."""
    return list(read_jsonl(Path(run_dir) / EVENTS_FILE))


class Heartbeat:
    """Rewrites `<run_dir>/heartbeat.json` every `interval` seconds.

    A thread, because the process spends its life blocked on an evaluator and
    the whole point is to keep saying "alive" while it is. A daemon thread,
    because a heartbeat must never be the thing that keeps a dead run's process
    around.
    """

    def __init__(
        self,
        run_dir: str | Path,
        *,
        session: str,
        interval: float = HEARTBEAT_INTERVAL_S,
    ) -> None:
        self.path = Path(run_dir) / HEARTBEAT_FILE
        self.session = session
        self.interval = float(interval)
        self._state: dict[str, Any] = {}
        self._beats = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, **state: Any) -> None:
        self.update(**state)
        self._beat()
        self._thread = threading.Thread(
            target=self._loop, name="evolvekit-heartbeat", daemon=True
        )
        self._thread.start()

    def update(self, **state: Any) -> None:
        """Change what the next beat says. Costs no write of its own."""
        with self._lock:
            self._state.update(state)

    def stop(self, **state: Any) -> None:
        """Stop beating, leaving one last beat that says how things ended."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, 2 * self.interval))
            self._thread = None
        self.update(**state)
        self._beat()

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            self._beat()

    def _beat(self) -> None:
        with self._lock:
            self._beats += 1
            payload = {
                "ts": utc_now(),
                "pid": os.getpid(),
                "session": self.session,
                "interval_s": self.interval,
                "beats": self._beats,
                **_jsonable(self._state),
            }
        try:
            _atomic_write(self.path, json.dumps(payload, allow_nan=False))
        except OSError:
            # On Windows `os.replace` fails while a reader has the file open.
            # The next beat is seconds away; a missed one is not worth a run.
            pass


def read_heartbeat(run_dir: str | Path) -> dict[str, Any] | None:
    """The last beat, or `None` when there is none to read."""
    path = Path(run_dir) / HEARTBEAT_FILE
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None
