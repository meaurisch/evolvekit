"""Evaluator runs that have been paid for once.

Every successful run of a stage command is kept under `work/cache/`, keyed by
what was run: the candidate's source and the flags it was given, the stage and
its command, the instance, the seed, and whether it was the hold-out. Running the same thing again is then
a lookup. That is what turns a crash at hour nine into the loss of the runs
that were in flight -- the resumed run evaluates the same children (the driver
writes them down before evaluating them) and finds the finished runs here --
and it is why the same configuration under a second candidate id costs nothing.

Only successes are kept. A crash or a timeout may be the machine's fault, and
an evaluation that is retried must actually be retried.

A name is not a content, so the key also holds a fingerprint of every file the
run reads that evolvekit can see: the instance, each input that is a file or a
directory, and each word of the stage command that names a file (the solver
script, the interpreter). Regenerating `f01.json` under the same name, or
fixing a bug in `solve.py`, is then a different run -- not an old result served
as a new measurement. A program found on `PATH`, and whatever the solver reads
on its own, are still outside the key: swapping those under a run directory is
a new experiment (`evaluate.cache: false`, or a new `--run-dir`).

The timeout is not in the key. A success is an answer under any timeout it
fits in, so `load` refuses only an entry that ran longer than today's timeout
allows: raising a timeout keeps the cache, lowering it cannot pass off a run
that would now have timed out.
"""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from pathlib import Path
from typing import Any

from evolvekit.evaluate.types import StageOutcome

__all__ = ["EvalCache", "fingerprint"]

_HASHED: dict[tuple[str, int, int], str] = {}
"""File content hashes by (path, size, mtime): a 0.5 MB instance is read once
per change, not once per evaluation."""
_HASHED_LOCK = threading.Lock()


def fingerprint(path: Path) -> str | None:
    """What is in `path` right now, or `None` when there is nothing there.

    A file by its content; a directory by the names, sizes and modification
    times of the files below it (hashing a whole instance set on every lookup
    would cost more than some evaluations)."""
    try:
        stat = path.stat()
    except OSError:
        return None
    if path.is_dir():
        listing = []
        for child in sorted(path.rglob("*")):
            try:
                if child.is_file():
                    child_stat = child.stat()
                    listing.append([child.relative_to(path).as_posix(), child_stat.st_size, child_stat.st_mtime_ns])
            except OSError:
                continue
        return "dir:" + hashlib.sha256(json.dumps(listing).encode("utf-8")).hexdigest()
    memo = (str(path.resolve()), stat.st_size, stat.st_mtime_ns)
    with _HASHED_LOCK:
        known = _HASHED.get(memo)
    if known is not None:
        return known
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
    except OSError:
        return None
    value = digest.hexdigest()
    with _HASHED_LOCK:
        _HASHED[memo] = value
    return value


def _files_read(
    cwd: Path | None, command: str, inputs: tuple[str, ...] | list[str], instance: str | None
) -> list[list[str | None]]:
    """`[name, fingerprint]` for everything the run reads that is a path here."""
    if cwd is None:
        return []
    names = [word.strip("\"'") for word in command.split() if "{" not in word]
    names += list(inputs)
    if instance is not None:
        names.append(instance)
    seen: dict[str, str | None] = {}
    for name in names:
        if not name or name in seen:
            continue
        path = Path(name) if Path(name).is_absolute() else Path(cwd) / name
        try:
            if not path.exists():
                continue
        except OSError:
            continue
        seen[name] = fingerprint(path)
    return [[name, value] for name, value in seen.items()]


class EvalCache:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)

    @staticmethod
    def key(
        *,
        stage_id: str,
        command: str,
        reading: tuple[Any, ...],
        flags: tuple[str, ...] = (),
        candidate_path: Path,
        inputs: tuple[str, ...] | list[str],
        instance: str | None,
        seed: int,
        private: bool,
        cwd: Path | None = None,
    ) -> str:
        try:
            source = hashlib.sha256(candidate_path.read_bytes()).hexdigest()
        except OSError:
            source = str(candidate_path)
        described = json.dumps(
            [stage_id, command, list(reading), list(flags), source, list(inputs), instance, seed, private,
             _files_read(cwd, command, inputs, instance)],
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(described.encode("utf-8")).hexdigest()[:32]

    def load(
        self, key: str, stage_id: str, private: bool, timeout: float | None = None
    ) -> StageOutcome | None:
        try:
            payload = json.loads((self.directory / f"{key}.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict) or not isinstance(payload.get("kpis"), dict):
            return None
        ran_for = payload.get("ran_for_s")
        if timeout is not None and isinstance(ran_for, (int, float)) and ran_for > timeout:
            return None  # it would have timed out under today's limit
        return StageOutcome(
            stage_id=stage_id,
            ok=True,
            kpis={str(k): float(v) for k, v in payload["kpis"].items()},
            vector_kpis={str(k): [float(x) for x in v] for k, v in (payload.get("vector_kpis") or {}).items()},
            text_feedback=str(payload.get("text_feedback") or ""),
            duration_s=0.0,  # a lookup costs no evaluator time; `ran_for_s` says what it once cost
            private=private,
            cached=True,
        )

    def store(self, key: str, outcome: StageOutcome) -> None:
        if not outcome.ok:
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "kpis": outcome.kpis,
            "vector_kpis": outcome.vector_kpis,
            "text_feedback": outcome.text_feedback,
            "ran_for_s": round(outcome.duration_s, 3),
        }
        path = self.directory / f"{key}.json"
        # Its own scratch file: two workers can finish the same configuration
        # under two candidate ids at the same moment.
        scratch = self.directory / f"{key}.{uuid.uuid4().hex}.tmp"
        try:
            scratch.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
            scratch.replace(path)  # a half-written entry must never be read as a result
        except OSError:  # the cache is a convenience; a result is never lost to it
            scratch.unlink(missing_ok=True)
