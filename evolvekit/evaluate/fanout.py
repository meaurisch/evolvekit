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

import math
import queue
import re
import threading
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from statistics import fmean, stdev, variance
from typing import Mapping, Sequence

from evolvekit.config import ConfigError, StageConfig
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
from evolvekit.stats import t_scale
from evolvekit.stopping import AnyEvent

__all__ = ["Job", "Race", "race_looks", "run_instance_stage", "per_instance_key"]


@dataclass(frozen=True)
class Job:
    """One candidate's turn at a per-instance stage."""

    candidate_id: str
    candidate_path: Path
    configuration: Configuration | None = None
    observer: Observer | None = None
    app: str | None = None
    """For a tree problem: what `{app}` is, the program inside this tree."""
    source_id: str | None = None
    """For a tree problem: the tree's commit, which keys the cache."""


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
    cv: float = 0.0
    """The objective's relative noise of one run on one instance, one side of
    the comparison (`Noise.vector_cv`); 0 when unknown."""
    instances: int = 0
    """How many instances the stage has, for the reason sentence; 0 (not
    given) falls back to the incumbent's count."""
    df: float = math.inf
    """The degrees of freedom `cv` is estimated on (`Noise.vector_cv_df`):
    the noise term's normal quantile widens to Student's t at them
    (Decision 17b). Infinite: a known cv, the normal rule."""
    by_seed: Mapping[int, Mapping[str, float]] = field(default_factory=dict)
    """The incumbent's own values per seed and instance (Decision 17a). A
    challenger's prefix is compared with the incumbent's values **on the
    seeds the prefix ran** (`against`), so on a repeatable stage the seed's
    effect cancels and the paired `cv` is the right noise. Empty (a file
    from before it): the challenger is compared with `incumbent`, the means
    over all the incumbent's seeds, and `cv` must allow for that."""

    def against(self, seeds: Mapping[str, Sequence[int]]) -> dict[str, float]:
        """What a prefix that ran `seeds[name]` on each instance is compared
        with: the incumbent's mean over those same seeds where `by_seed` has
        them all, else its mean over all its seeds (`incumbent`)."""
        out: dict[str, float] = {}
        for name, ran in seeds.items():
            values = [self.by_seed.get(seed, {}).get(name) for seed in ran]
            if self.by_seed and ran and all(v is not None for v in values):
                out[name] = fmean(v for v in values if v is not None)
            elif name in self.incumbent:
                out[name] = self.incumbent[name]
        return out

    @property
    def population(self) -> int:
        """The n the race reads over: the instances it can compare on, those
        where the incumbent is above 0 (no ratio at 0), never more than the
        stage has. So `compared` can reach n, and the finite-population factor 0."""
        positive = sum(1 for value in self.incumbent.values() if value > 0)
        return min(self.instances, positive) if self.instances > 0 else positive

    @property
    def looks(self) -> tuple[int, ...]:
        """After `after` compared instances, then every quarter of them, never at
        the last: a candidate with nothing left to run cannot be raced out."""
        return race_looks(self.after, self.population)

    def _diffs(self, mine: Mapping[str, float], against: Mapping[str, float] | None = None) -> list[float]:
        """Per instance both have (the incumbent's above 0): how far `mine` is
        behind, as a fraction -- positive is worse, whatever the direction.
        `against` is what the incumbent reached on the prefix's own seeds
        (`against()`); `None`, its means (`incumbent`)."""
        sign = 1.0 if self.minimize else -1.0
        reference = self.incumbent if against is None else against
        return [
            sign * (mine[name] / reference[name] - 1.0)
            for name in mine
            if name in self.incumbent and self.incumbent[name] > 0 and name in reference and reference[name] > 0
        ]

    def behind_pct(self, mine: Mapping[str, float], against: Mapping[str, float] | None = None) -> tuple[int, float]:
        """On the instances both have a value for: how many, and how many
        percent worse `mine` is than the incumbent, averaged over them."""
        diffs = self._diffs(mine, against)
        if not diffs:
            return 0, 0.0
        return len(diffs), 100.0 * fmean(diffs)

    def margin_at(self, diffs: Sequence[float]) -> float:
        """How far behind, in percent, a candidate may be at a look over
        `diffs` (`_diffs`, fractions): the largest of `margin_pct`, twice the
        run-to-run noise of the mean's ratio (`200·cv`: one side at
        `cv`, the other at `cv`, a mean over m instances at `√2·cv/√m`,
        so this is `z = √(2m)` sd of it, 2.8 at m = 4; with `cv` estimated
        on `df` degrees of freedom it is widened by `stats.t_scale(z, df)`,
        so that it is crossed as rarely as at a known cv), and twice the
        standard error of the cases' own spread, with the finite-population
        factor that makes it 0 once every instance is in."""
        m, n = len(diffs), self.population
        spread = 0.0
        if m >= 2 and n > 0:
            spread = 200.0 * stdev(diffs) / math.sqrt(m) * math.sqrt(max(0.0, 1.0 - m / n))
        noise = 200.0 * self.cv
        if noise > 0 and m >= 1:
            noise *= t_scale(math.sqrt(2.0 * m), self.df)
        return max(self.margin_pct, noise, spread)

    def judge(self, mine: Mapping[str, float], against: Mapping[str, float] | None = None) -> tuple[str, float] | None:
        """`decide`, with the margin it used (percent, to two places)."""
        diffs = self._diffs(mine, against)
        compared = len(diffs)
        if compared not in self.looks:
            return None
        behind, margin = 100.0 * fmean(diffs), self.margin_at(diffs)
        if behind <= margin:
            return None
        shown = round(margin, 2)
        reason = (
            f"raced out after {compared} of {self.instances or self.population} instances: "
            f"{behind:.2f} % behind the best "
            f"candidate so far on the same instances (margin {shown:g} %)"
        )
        return reason, shown

    def decide(self, mine: Mapping[str, float], against: Mapping[str, float] | None = None) -> str | None:
        """The reason `mine` is raced out at this look; `None` when it is not
        a look or `mine` is within the margin."""
        verdict = self.judge(mine, against)
        return None if verdict is None else verdict[0]


def race_looks(after: int, instances: int) -> tuple[int, ...]:
    """Where a race reads: after `after` instances, then every ceil(n / 4),
    never at `n` itself."""
    if after >= instances:
        return ()
    return tuple(range(after, instances, math.ceil(instances / 4)))


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
    stop: threading.Event | None = None,
) -> dict[str, StageOutcome]:
    """Run `stage` for every job, instance and seed; one outcome per candidate.

    `seeds` replaces the stage's own `0 .. seeds-1` -- a comparison on seeds
    the search never saw names them. `keep_going` runs a candidate's remaining
    instances after one has failed: a search has no use for them, a comparison
    does, because every pair that did finish still counts. `stop` is the run's
    own stop (`evolvekit/stopping.py`): once set, runs in flight are stopped,
    no other is started, and what did not finish is *abandoned*, not failed.

    A `pin_cpus` this machine cannot honour is a `ConfigError` here, before
    the first run: the config may have been written for another machine, and
    runs that silently share cores would be scored as if they had not.
    """
    missing = stage.missing_cpus()
    if missing is not None:
        raise ConfigError(missing)
    instances = stage.private_instances if private else stage.instances
    names = stage.instance_names(private)
    files = _file_names(names)
    seed_values = list(seeds) if seeds is not None else list(range(stage.seeds))
    cancel = threading.Event()
    halt = AnyEvent(cancel, stop)
    board = _Board(jobs, len(instances), seed_values, race=race, names=names)
    cpus: queue.SimpleQueue[int] | None = None
    if stage.pin_cpus:
        cpus = queue.SimpleQueue()
        for cpu in stage.pin_cpus[: stage.workers]:
            cpus.put(cpu)

    def run(job: Job, index: int, seed: int) -> None:
        if halt.is_set() or (board.failed(job.candidate_id) and not keep_going):
            return  # a candidate that has already failed buys nothing further
        cpu = cpus.get() if cpus is not None else None
        try:
            for attempt in range(stage.retries + 1):
                outcome = _run_once(
                    job.candidate_path,
                    stage,
                    inputs=stage.private_inputs if private else stage.inputs,
                    out_path=_unit_path(out_dir, job, stage, files[index], seed, attempt, private),
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
                        cancel=halt,
                    ),
                    cache=cache,
                    app=job.app,
                    source_id=job.source_id,
                )
                board.ran(job.candidate_id, outcome)
                if outcome.ok or halt.is_set() or (board.failed(job.candidate_id) and not keep_going):
                    break
            verdict = board.settle(job.candidate_id, index, seed, names[index], outcome)
            if verdict is not None and job.observer is not None:
                job.observer(
                    "raced_out",
                    reason=verdict,
                    runs_done=board.runs_done(job.candidate_id),
                    looks=list(race.looks) if race is not None else [],
                    margin_pct=board.margin(job.candidate_id),
                )
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


def _file_names(names: Sequence[str]) -> tuple[str, ...]:
    """What each instance is called in a file name: its name, made safe for one.

    Making a name safe can make two of them the same -- `a b` and `a_b` both
    become `a_b` -- and so can a file system that ignores case (`A` and `a` on
    Windows). Two instances sharing a file name share their `{out}` and their
    logs: the second run overwrites the first one's logs, and with two workers
    one instance can read the other's result and report it as its own. So when
    any two collide, every instance gets its position in the list appended
    (`a_b-0`, `a_b-1`): unique, because the suffix is, and still readable.
    Names that do not collide are left alone.
    """
    safe = [re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or "instance" for name in names]
    if len({name.lower() for name in safe}) == len(safe):
        return tuple(safe)
    return tuple(f"{name}-{index}" for index, name in enumerate(safe))


def _unit_path(
    out_dir: Path, job: Job, stage: StageConfig, file_name: str, seed: int, attempt: int, private: bool
) -> Path:
    """One file per run and per attempt: a retry must not overwrite the logs of
    the failure that caused it. `file_name` comes from `_file_names`."""
    parts = [job.candidate_id, stage.id]
    if private:
        parts.append("private")
    parts += [file_name, f"seed{seed}"]
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
        self._looked: dict[str, int] = {}
        """Per candidate, the last look the race read."""
        self._margins: dict[str, float] = {}
        """Per raced-out candidate, the margin it was raced out by (percent)."""

    def margin(self, candidate_id: str) -> float | None:
        with self._lock:
            return self._margins.get(candidate_id)

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
        seeds_of: dict[str, list[int]] = {}
        for (index, seed), done in self._done[candidate_id].items():
            if race.objective in done.kpis:
                by_instance.setdefault(self._names[index], []).append(done.kpis[race.objective])
                seeds_of.setdefault(self._names[index], []).append(seed)
        prefix = {name: fmean(values) for name, values in by_instance.items()}
        # The incumbent on the prefix's own seeds (Decision 17a): units run seed
        # by seed, so every look reads seed 0, and on a repeatable stage the
        # seed's effect is then the same on both sides.
        against = race.against(seeds_of)
        compared, _ = race.behind_pct(prefix, against)
        # One read per look: a later seed's run, or a parallel worker's, can
        # land while `compared` still stands at a look already read -- a second
        # read there is a second test of the same evidence.
        if compared not in race.looks or compared >= self._instances or compared <= self._looked.get(candidate_id, 0):
            return None
        self._looked[candidate_id] = compared
        verdict = race.judge(prefix, against)
        if verdict is None:
            return None
        reason, self._margins[candidate_id] = verdict
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
            # Only a stopped stage leaves runs unstarted without a failure: the
            # run's time limit or a stop request (Ctrl+C never gets here).
            failure = StageOutcome(
                stage_id=stage.id, ok=False, private=private, abandoned=True,
                failure=f"called off after {len(done)} of {self._instances * self._seeds} runs: the run was stopped",
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
    kpi_cv: dict[str, float | None] = {}
    vectors: dict[str, list[float]] = {}
    kpi_var: dict[str, float] = {}
    vector_var: dict[str, list[float]] = {}
    seed_runs: dict[str, list[float]] = {}
    seed_vectors: dict[str, list[list[float]]] = {}
    for key in sorted(keys):
        by_instance = [
            [done[(index, seed)].kpis[key] for seed in seeds] for index in range(len(names))
        ]
        vectors[per_instance_key(key)] = [fmean(values) for values in by_instance]
        kpis[key] = fmean(vectors[per_instance_key(key)])
        # Per seed, what that seed's runs reached on every instance, and the
        # stage-level value of that one run: the seed's are the reference of a
        # later candidate's paired difference on the same seed.
        rows = [[done[(index, seed)].kpis[key] for index in range(len(names))] for seed in seeds]
        seed_vectors[per_instance_key(key)] = rows
        seed_runs[key] = [fmean(row) for row in rows]
        if len(seeds) > 1:
            cvs = [cv for cv in (_cv(values) for values in by_instance) if cv is not None]
            kpi_cv[key] = fmean(cvs) if cvs else None
            # Each instance's variance over its seeds; one run of the stage is
            # the mean of M independent instance runs, so its variance is
            # the mean of theirs over M.
            vector_var[per_instance_key(key)] = [variance(values) for values in by_instance]
            kpi_var[key] = fmean(vector_var[per_instance_key(key)]) / len(names)
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
        kpi_var=kpi_var,
        vector_var=vector_var,
        seed_runs=seed_runs,
        seed_vectors=seed_vectors,
        seed_list=tuple(seeds),
        instance_durations=[
            fmean(done[(index, seed)].duration_s for seed in seeds) for index in range(len(names))
        ],
        # Three instances' worth: a prompt is not a log.
        text_feedback="\n".join(notes[:3])[:FEEDBACK_LIMIT],
        stderr=first.stderr,
        stdout=first.stdout,
        private=private,
        instance_names=tuple(names),
    )
