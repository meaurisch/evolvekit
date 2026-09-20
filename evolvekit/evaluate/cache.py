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

The key does not see the solver itself: a run directory is one experiment, and
swapping the binary under it is a new experiment (`evaluate.cache: false`, or a
new `--run-dir`).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from evolvekit.evaluate.types import StageOutcome

__all__ = ["EvalCache"]


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
    ) -> str:
        try:
            source = hashlib.sha256(candidate_path.read_bytes()).hexdigest()
        except OSError:
            source = str(candidate_path)
        described = json.dumps(
            [stage_id, command, list(reading), list(flags), source, list(inputs), instance, seed, private],
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(described.encode("utf-8")).hexdigest()[:32]

    def load(self, key: str, stage_id: str, private: bool) -> StageOutcome | None:
        try:
            payload = json.loads((self.directory / f"{key}.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict) or not isinstance(payload.get("kpis"), dict):
            return None
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
