"""A stage that runs once per instance.

The classic stage hands the evaluator a whole input set and gets one JSON
object back; what happened on which instance stays inside the evaluator. A stage
that lists `instances` is run once per instance and seed instead -- each run a
short-lived process of its own -- and that is what makes four things possible
that an expensive, time-limited solver needs:

* **Parallelism that does not distort the objective.** The runs are independent,
  so `workers` of them can be in flight at once, each pinned to a CPU of its own
  (`pin_cpus`). The pool spans the whole generation, not one candidate: six
  candidates on ten instances are sixty runs for three workers, and no worker
  idles while another finishes a candidate's last instance.
* **A failure with an address.** A crash is *this* instance, *this* seed, *this*
  attempt, with its own log files; it is retried on its own (`retries`), and a
  candidate that has failed for good stops costing anything -- its remaining
  runs are never started.
* **A per-instance picture.** Every run reports under its instance's name, so
  the status document can say where a candidate wins and where it loses without
  the evaluator having to emit a list.
* **Comparable instances.** `normalize: baseline` (see `Cascade`) needs the
  value per instance, which only a per-instance stage knows.

Runs are ordered seed by seed and instance by instance *across* candidates, so
at any moment all candidates have been measured on the same instances -- the
order a racing rule needs, and the one that makes a half-finished generation
readable.
"""

from __future__ import annotations

import queue
import re
import threading
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean
from typing import Mapping, Sequence

from evolvekit.config import StageConfig
from evolvekit.evaluate.cache import EvalCache
from evolvekit.evaluate.stages import (
    FEEDBACK_LIMIT,
    Configuration,
    Observer,
    UnitContext,
    _cv,
    _run_once,
)
from evolvekit.evaluate.types import StageOutcome

__all__ = ["Job", "Race", "run_instance_stage", "per_instance_key"]


@dataclass(frozen=True)
class Job:
    """One candidate's turn at a per-instance stage."""

    candidate_id: str
    candidate_path: Path
    configuration: Configuration | None = None
    observer: Observer | None = None


@dataclass(frozen=True)
class Race:
    """A stage's `race` rule, with what it is raced against."""

    objective: str
    minimize: bool
    incumbent: Mapping[str, float]
    """The best candidate's objective so far, by instance name, at this stage."""
    after: int
    margin_pct: float
    exempt: frozenset[str] = frozenset()
    """Candidates that always finish: the baseline is a yardstick, not a contestant."""

    def behind_pct(self, mine: Mapping[str, float]) -> tuple[int, float]:
        """On the instances both have a value for: how many, and how many
        percent worse `mine` is than the incumbent, averaged over them."""
        ratios = [
            mine[name] / self.incumbent[name]
            for name in mine
            if name in self.incumbent and self.incumbent[name] > 0
        ]
        if not ratios:
            return 0, 0.0
        relative = fmean(ratios) - 1.0
        return len(ratios), 100.0 * (relative if self.minimize else -relative)


def per_instance_key(kpi: str) -> str:
    """Where a scalar KPI's per-instance values live in `vector_kpis`."""
    return f"{kpi}_per_instance"


def run_instance_stage(
    jobs: Sequence[Job],
    stage: StageConfig,
    *,
    out_dir: Path,
    cwd: Path,
    private: bool = False,
    required_kpis: tuple[str, ...] = (),
    cache: EvalCache | None = None,
    seeds: Sequence[int] | None = None,
    keep_going: bool = False,
    race: Race | None = None,
) -> dict[str, StageOutcome]:
    """Run `stage` for every job, instance and seed; one outcome per candidate.

    `seeds` replaces the stage's own `0 .. seeds-1` -- a comparison on seeds
    the search never saw names them. `keep_going` runs a candidate's remaining
    instances after one has failed: a search has no use for them, a comparison
    does, because every pair that did finish still counts.
    """
    instances = stage.private_instances if private else stage.instances
    names = stage.instance_names(private)
    seed_values = list(seeds) if seeds is not None else list(range(stage.seeds))
    cancel = threading.Event()
    board = _Board(jobs, len(instances), seed_values, race=race, names=names)
    cpus: queue.SimpleQueue[int] | None = None
    if stage.pin_cpus:
        cpus = queue.SimpleQueue()
        for cpu in stage.pin_cpus[: stage.workers]:
            cpus.put(cpu)

    def run(job: Job, index: int, seed: int) -> None:
        if cancel.is_set() or (board.failed(job.candidate_id) and not keep_going):
            return  # a candidate that has already failed buys nothing further
        cpu = cpus.get() if cpus is not None else None
        try:
            for attempt in range(stage.retries + 1):
                outcome = _run_once(
                    job.candidate_path,
                    stage,
                    inputs=stage.private_inputs if private else stage.inputs,
                    out_path=_unit_path(out_dir, job, stage, names[index], seed, attempt, private),
                    cwd=cwd,
                    private=private,
                    seed=seed,
                    required_kpis=required_kpis,
                    observer=job.observer,
                    configuration=job.configuration,
                    unit=UnitContext(
                        instance=instances[index],
                        name=names[index],
                        attempt=attempt,
                        cpus=(cpu,) if cpu is not None else (),
                        cancel=cancel,
                    ),
                    cache=cache,
                )
                board.ran(job.candidate_id, outcome)
                if outcome.ok or cancel.is_set() or (board.failed(job.candidate_id) and not keep_going):
                    break
            verdict = board.settle(job.candidate_id, index, seed, names[index], outcome)
            if verdict is not None and job.observer is not None:
                job.observer("raced_out", reason=verdict, runs_done=board.runs_done(job.candidate_id))
        finally:
            if cpus is not None and cpu is not None:
                cpus.put(cpu)

    units = [
        (job, index, seed)
        for seed in seed_values
        for index in range(len(instances))
        for job in jobs
    ]
    if stage.workers <= 1:
        for unit in units:  # on this thread: Ctrl+C reaches the run it interrupts
            run(*unit)
    else:
        with ThreadPoolExecutor(max_workers=stage.workers, thread_name_prefix="evaluate") as pool:
            futures = [pool.submit(run, *unit) for unit in units]
            try:
                wait(futures)
            except BaseException:
                # Ctrl+C lands here, on the waiting thread. The runs in flight
                # are on other threads and would each go on to their timeout.
                cancel.set()
                wait(futures)
                raise
            for future in futures:
                future.result()  # a bug in `run` is a bug, not a missing result
    return {job.candidate_id: board.outcome(job.candidate_id, stage, names, private) for job in jobs}


def _unit_path(
    out_dir: Path, job: Job, stage: StageConfig, name: str, seed: int, attempt: int, private: bool
) -> Path:
    """One file per run and per attempt: a retry must not overwrite the logs of
    the failure that caused it."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or "instance"
    parts = [job.candidate_id, stage.id]
    if private:
        parts.append("private")
    parts += [safe, f"seed{seed}"]
    if attempt:
        parts.append(f"try{attempt}")
    return out_dir / (".".join(parts) + ".json")


class _Board:
    """What has come back so far, per candidate. Shared by the worker threads."""

    def __init__(
        self,
        jobs: Sequence[Job],
        instances: int,
        seeds: Sequence[int],
        *,
        race: Race | None = None,
        names: Sequence[str] = (),
    ) -> None:
        self._race = race
        self._names = list(names)
        self._lock = threading.Lock()
        self._instances = instances
        self._seed_values = list(seeds)
        self._seeds = len(self._seed_values)
        self._done: dict[str, dict[tuple[int, int], StageOutcome]] = {
            job.candidate_id: {} for job in jobs
        }
        self._failure: dict[str, StageOutcome] = {}
        self._runs: dict[str, int] = {job.candidate_id: 0 for job in jobs}
        self._spent: dict[str, float] = {job.candidate_id: 0.0 for job in jobs}

    def failed(self, candidate_id: str) -> bool:
        with self._lock:
            return candidate_id in self._failure

    def ran(self, candidate_id: str, outcome: StageOutcome) -> None:
        with self._lock:
            self._runs[candidate_id] += 1
            self._spent[candidate_id] += outcome.duration_s

    def runs_done(self, candidate_id: str) -> int:
        with self._lock:
            return len(self._done[candidate_id])

    def settle(
        self, candidate_id: str, index: int, seed: int, name: str, outcome: StageOutcome
    ) -> str | None:
        """Record one run. Returns the reason when this run raced the candidate out."""
        with self._lock:
            if outcome.ok:
                self._done[candidate_id][(index, seed)] = outcome
                return self._race_verdict(candidate_id, outcome.stage_id, outcome.private)
            if candidate_id not in self._failure:
                where = f"instance {name}" + (f", seed {seed}" if self._seeds > 1 else "")
                outcome.failure = f"{outcome.failure} ({where})"
                self._failure[candidate_id] = outcome
        return None

    def _race_verdict(self, candidate_id: str, stage_id: str, private: bool) -> str | None:
        race = self._race
        if race is None or candidate_id in race.exempt or candidate_id in self._failure:
            return None
        by_instance: dict[str, list[float]] = {}
        for (index, _seed), done in self._done[candidate_id].items():
            if race.objective in done.kpis:
                by_instance.setdefault(self._names[index], []).append(done.kpis[race.objective])
        compared, behind = race.behind_pct({name: fmean(values) for name, values in by_instance.items()})
        if compared < race.after or compared >= self._instances or behind <= race.margin_pct:
            return None
        reason = (
            f"raced out after {compared} of {self._instances} instances: {behind:.2f} % behind the best "
            f"candidate so far on the same instances (margin {race.margin_pct:g} %)"
        )
        # Kept where a failure is kept, so that its remaining runs are never
        # started -- but marked as what it is: nothing went wrong.
        self._failure[candidate_id] = StageOutcome(
            stage_id=stage_id, ok=False, skipped=True, private=private, failure=reason, raced_out=reason
        )
        return reason

    def outcome(
        self, candidate_id: str, stage: StageConfig, names: Sequence[str], private: bool
    ) -> StageOutcome:
        with self._lock:
            runs, spent = self._runs[candidate_id], self._spent[candidate_id]
            failure = self._failure.get(candidate_id)
            done = dict(self._done[candidate_id])
        if failure is None and len(done) < self._instances * self._seeds:
            # Only an interrupted stage leaves runs unstarted without a failure.
            failure = StageOutcome(
                stage_id=stage.id, ok=False, private=private,
                failure=f"called off after {len(done)} of {self._instances * self._seeds} runs",
            )
        if failure is not None:
            failure.duration_s, failure.runs = spent, runs
            return failure
        combined = _combine(stage, names, done, self._seed_values, private)
        combined.duration_s, combined.runs = spent, runs
        return combined


def _combine(
    stage: StageConfig,
    names: Sequence[str],
    done: dict[tuple[int, int], StageOutcome],
    seeds: Sequence[int],
    private: bool,
) -> StageOutcome:
    """Seeds average within an instance, instances average into the stage.

    Every instance weighs the same whatever its seeds did, and the spread that
    is reported is the spread *between seeds of one instance* -- the noise of
    the solver -- not the spread between instances, which is no noise at all.
    """
    keys = set.intersection(*(set(outcome.kpis) for outcome in done.values()))
    kpis: dict[str, float] = {}
    kpi_cv: dict[str, float] = {}
    vectors: dict[str, list[float]] = {}
    for key in sorted(keys):
        by_instance = [
            [done[(index, seed)].kpis[key] for seed in seeds] for index in range(len(names))
        ]
        vectors[per_instance_key(key)] = [fmean(values) for values in by_instance]
        kpis[key] = fmean(vectors[per_instance_key(key)])
        if len(seeds) > 1:
            kpi_cv[key] = fmean(_cv(values) for values in by_instance)
    notes = []
    for index in range(len(names)):  # one note per instance: its seeds say the same thing
        said = next((done[(index, s)].text_feedback for s in seeds if done[(index, s)].text_feedback), "")
        if said:
            notes.append(f"{names[index]}: {said}")
    first = done[(0, seeds[0])]
    return StageOutcome(
        stage_id=stage.id,
        ok=True,
        kpis=kpis,
        vector_kpis=vectors,
        kpi_cv=kpi_cv,
        # Three instances' worth: a prompt is not a log.
        text_feedback="\n".join(notes[:3])[:FEEDBACK_LIMIT],
        stderr=first.stderr,
        stdout=first.stdout,
        private=private,
        instance_names=tuple(names),
    )
