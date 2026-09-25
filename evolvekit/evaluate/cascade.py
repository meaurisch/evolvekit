"""The staged evaluator: cheap rungs for everyone, expensive rungs for the few.

`stage[i].promote` governs who moves from stage i to stage i+1. Stage 0 is
`builtin-static` and always promotes everyone it does not reject -- it has no
score to rank by, and letting a static pass masquerade as a good score would
put unevaluated candidates at the top of the archive.

A candidate keeps the score of the deepest stage it actually reached. Not being
promoted is not a punishment; failing a stage is (`evaluate.failure_score`).
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from statistics import fmean, quantiles
from typing import Any, Callable, Iterable, Sequence

from evolvekit.budget import BudgetGuard
from evolvekit.candidate import SEED_OPERATOR, Candidate
from evolvekit.config import Config, PromoteRule, StageConfig
from evolvekit.evaluate.cache import EvalCache
from evolvekit.evaluate.fanout import Job, Race, per_instance_key, run_instance_stage
from evolvekit.evaluate.scoring import compute_score, ranking_score
from evolvekit.evaluate.signature import BehaviourIndex, behaviour_signature
from evolvekit.evaluate.stages import (
    FEEDBACK_LIMIT,
    STDERR_LIMIT,
    Configuration,
    run_command_stage,
    run_static_stage,
)
from evolvekit.evaluate.types import EvalResult, StageOutcome
from evolvekit.ledger import _atomic_write

__all__ = [
    "Cascade",
    "select_promoted",
    "archive_threshold",
    "finished_final_stage",
    "race_warnings",
]


def finished_final_stage(
    config: Config,
    *,
    rejected: bool,
    last_failure: str | None,
    stages_reached: Sequence[str],
    private_score: float | None,
) -> bool:
    """Whether a candidate's score may be compared with anybody else's.

    A score means something only next to scores from the same stage on the
    same inputs, so a candidate competes -- is ranked, archived, bred from --
    only when it got all the way through. That rules out four cases which used
    to be ranked as if they had:

    * a **failed** evaluation. It carries `evaluate.failure_score`, and the
      default `-1000` outranks every healthy candidate whose minimised cost is
      above 1000;
    * a candidate **not promoted** past a cheaper stage, whose score comes from
      a different input set;
    * a final stage **skipped** by `budget.max_full_evals_per_day`;
    * a **hold-out run that failed**, which would otherwise keep its public
      score with no discount -- the one thing the hold-out is there to prevent.

    Takes plain values rather than an `EvalResult` so that a `runs.jsonl` row
    written before the `competes` field existed can be judged by the same rule.
    """
    if rejected or last_failure:
        return False
    final = config.final_stage
    if final.id not in stages_reached:
        return False
    held_out = final.private_inputs or final.private_instances
    if final.kind == "command" and held_out and private_score is None:
        return False
    return True


def race_warnings(config: Config) -> list[str]:
    """A `race` rule that can never stop anyone, said in words.

    A candidate is raced out once it has finished `after` instances and still
    has at least one left. With `after` at or above the stage's number of
    instances that moment never comes, and the rule does nothing -- a setting
    the user believes is saving solver time, silently inert."""
    warnings = []
    for stage in config.evaluate.stages:
        count = len(stage.instance_names())
        if stage.race is not None and stage.race.after >= count:
            warnings.append(
                f"stage {stage.id}: race.after is {stage.race.after}, but the stage has {count} "
                "instance(s): no candidate can ever be raced out. Set `after` below the number of "
                "instances, or remove `race`"
            )
    return warnings


def archive_threshold(scores: Sequence[float], percentile: float) -> float | None:
    """The `percentile`-th percentile of prior archive scores, or None if empty."""
    finite = [float(s) for s in scores]
    if not finite:
        return None
    if len(finite) == 1 or percentile <= 0:
        return min(finite)
    if percentile >= 100:
        return max(finite)
    cuts = quantiles(sorted(finite), n=100, method="inclusive")
    return cuts[int(percentile) - 1] if 1 <= int(percentile) <= 99 else max(finite)


def select_promoted(
    scored: Sequence[tuple[str, float]],
    rule: PromoteRule,
    archive_scores: Sequence[float] = (),
) -> list[str]:
    """Apply one promote rule to `(candidate_id, score)` pairs.

    An empty rule promotes everything. When both sub-rules are set a candidate
    only needs to satisfy one, matching the proposal's `top_k … or score >= p`.
    """
    ids = [cid for cid, _ in scored]
    if rule.top_k_per_generation is None and rule.archive_percentile is None:
        return ids

    chosen: set[str] = set()
    if rule.top_k_per_generation is not None:
        ranked = sorted(scored, key=lambda item: (-item[1], item[0]))
        chosen.update(cid for cid, _ in ranked[: rule.top_k_per_generation])
    if rule.archive_percentile is not None:
        threshold = archive_threshold(archive_scores, rule.archive_percentile)
        if threshold is None:
            chosen.update(ids)  # nothing to compare against yet
        else:
            chosen.update(cid for cid, score in scored if score >= threshold)
    return [cid for cid in ids if cid in chosen]


class Cascade:
    """Runs one generation of candidates through every configured stage."""

    def __init__(
        self,
        config: Config,
        *,
        work_dir: Path,
        budget: BudgetGuard | None = None,
        signatures: BehaviourIndex | None = None,
        on_event: Callable[..., Any] | None = None,
    ) -> None:
        self.config = config
        self.on_event = on_event
        """`on_event(type, **fields)`, called for every evaluator run as it starts
        and ends. The driver points it at the run's event log."""
        self.work_dir = Path(work_dir)
        self.candidates_dir = self.work_dir / "candidates"
        self.candidates_dir.mkdir(parents=True, exist_ok=True)
        self.budget = budget
        # Shared across generations and owned by the driver when there is one:
        # the seed is a perfectly good twin target, and so is a candidate from
        # six generations ago.
        self.signatures = signatures if signatures is not None else BehaviourIndex()
        self._configurations: dict[str, Configuration] = {}
        """Per candidate, the validated parameters as a command can take them.
        Filled when the static stage resolves them (`problem.parameters`)."""
        self.cache = EvalCache(self.work_dir / "cache") if config.evaluate.cache else None
        """Successful evaluator runs, kept so that an identical one is a lookup:
        what makes an interrupted generation cheap to finish."""
        self.incumbent: dict[str, dict[str, Any]] = self._load_json(self.work_dir / "incumbent.json")
        """Per stage, the best candidate so far and what it reached on each
        instance: what a stage's `race` rule measures a candidate against."""
        self.reference: dict[str, dict[str, float]] = self._load_reference()
        """Per stage, what the baseline reached on each instance: the yardstick
        of `normalize: baseline`. Kept on disk, because a resumed run does not
        evaluate its seed again."""

    # -- public ----------------------------------------------------------

    def evaluate_generation(
        self,
        candidates: Sequence[Candidate],
        archive_scores: Iterable[float] = (),
    ) -> dict[str, EvalResult]:
        """Evaluate a whole generation; returns results keyed by candidate id."""
        prior = list(archive_scores)
        results: dict[str, EvalResult] = {
            c.id: EvalResult(score=self.config.evaluate.failure_score) for c in candidates
        }
        paths = {c.id: self._materialise(c) for c in candidates}

        stages = self.config.evaluate.stages
        alive = [c.id for c in candidates]
        by_id = {c.id: c for c in candidates}

        for index, stage in enumerate(stages):
            if not alive:
                break
            survivors: list[str] = []
            began = self._stage_started(stage, alive, private=False)
            outcomes = (
                self._run_instances(stage, [by_id[cid] for cid in alive], paths)
                if stage.fans_out
                else None
            )
            failed = raced_out = 0
            for cid in alive:
                outcome = (
                    outcomes[cid]
                    if outcomes is not None
                    else self._run_stage(stage, by_id[cid], paths[cid])
                )
                # Raced out is not failed: nothing went wrong, the candidate
                # was stopped because it was behind.
                if outcome.raced_out:
                    raced_out += 1
                elif not outcome.ok:
                    failed += 1
                self._absorb(results[cid], outcome, stage)
                if outcome.params is not None:
                    self._configure(cid, paths[cid], outcome.params)
                if not outcome.ok:
                    continue
                # A candidate that behaved exactly like one already evaluated
                # at this stage keeps its score, but buys nothing further: no
                # deeper stage, no hold-out run, no archive entry.
                if self._note_behaviour(results[cid], outcome, stage, cid):
                    continue
                survivors.append(cid)

            self._stage_finished(stage, began, len(alive), failed, private=False, raced_out=raced_out)
            is_final = index == len(stages) - 1
            if is_final:
                self._run_private(stage, by_id, paths, results, survivors)
                break

            scored = [(cid, results[cid].score) for cid in survivors]
            alive = select_promoted(scored, stage.promote, prior)

        for result in results.values():
            result.competes = finished_final_stage(
                self.config,
                rejected=result.rejected,
                last_failure=result.last_failure,
                stages_reached=result.stages_reached,
                private_score=result.private_score,
            )
        return results

    # -- internals -------------------------------------------------------

    def _configure(self, candidate_id: str, path: Path, params: dict) -> None:
        """Keep a validated configuration in the shapes a command can take it:
        flags for `{params}`, and a JSON file beside the candidate for
        `{params_json}`."""
        space = self.config.problem.parameters
        assert space is not None
        json_path = path.with_suffix(".params.json")
        json_path.write_text(json.dumps(params, indent=2) + "\n", encoding="utf-8", newline="\n")
        self._configurations[candidate_id] = Configuration(
            flags=tuple(space.render_flags(params)), json_path=json_path
        )

    def _materialise(self, candidate: Candidate) -> Path:
        path = self.candidates_dir / f"{candidate.id}.py"
        path.write_text(candidate.source, encoding="utf-8", newline="\n")
        return path

    def _run_stage(
        self, stage: StageConfig, candidate: Candidate, path: Path
    ) -> StageOutcome:
        if stage.kind == "builtin-static":
            return run_static_stage(path, candidate.source, stage, self.config.problem)
        if self._final_stage_blocked(stage):
            return StageOutcome(
                stage_id=stage.id,
                ok=False,
                skipped=True,
                failure="daily full-evaluation cap reached",
            )
        outcome = run_command_stage(
            path,
            stage,
            inputs=stage.inputs,
            out_path=self.work_dir / "stage_out" / f"{candidate.id}.{stage.id}.json",
            cwd=self.config.base_dir,
            required_kpis=(self.config.evaluate.score.objective,),
            observer=self._observer(candidate.id, stage, private=False),
            configuration=self._configurations.get(candidate.id),
            cache=self.cache,
        )
        if self._is_final(stage) and self.budget is not None:
            self.budget.record_full_eval()
        return outcome

    def _stage_started(self, stage: StageConfig, candidates: Sequence[str], *, private: bool) -> float:
        """Tell the event log how much work a stage is about to be for this
        generation -- a long stage is otherwise a progress bar with no length."""
        if self.on_event is not None and stage.kind == "command":
            instances = stage.private_instances if private else stage.instances
            self.on_event(
                "stage_started",
                stage=stage.id,
                private=private,
                candidates=list(candidates),
                runs_per_candidate=max(1, len(instances)) * stage.seeds,
                workers=stage.workers,
            )
        return time.perf_counter()

    def _stage_finished(
        self, stage: StageConfig, began: float, candidates: int, failed: int, *, private: bool,
        raced_out: int = 0,
    ) -> None:
        if self.on_event is not None and stage.kind == "command":
            self.on_event(
                "stage_finished",
                stage=stage.id,
                private=private,
                candidates=candidates,
                failed=failed,
                raced_out=raced_out,
                duration_s=round(time.perf_counter() - began, 3),
            )

    def _run_instances(
        self,
        stage: StageConfig,
        candidates: Sequence[Candidate],
        paths: dict[str, Path],
        *,
        private: bool = False,
    ) -> dict[str, StageOutcome]:
        """A per-instance stage, for every candidate still alive at once: the
        worker pool spans the generation (`evaluate/fanout.py`)."""
        outcomes: dict[str, StageOutcome] = {}
        admitted: list[Candidate] = []
        for candidate in candidates:
            if not private and self._final_stage_blocked(stage):
                outcomes[candidate.id] = StageOutcome(
                    stage_id=stage.id,
                    ok=False,
                    skipped=True,
                    failure="daily full-evaluation cap reached",
                )
                continue
            if not private and self._is_final(stage) and self.budget is not None:
                self.budget.record_full_eval()  # counted on admission: the cap is about starts
            admitted.append(candidate)
        outcomes.update(
            run_instance_stage(
                [
                    Job(
                        candidate_id=c.id,
                        candidate_path=paths[c.id],
                        configuration=self._configurations.get(c.id),
                        observer=self._observer(c.id, stage, private=private),
                    )
                    for c in admitted
                ],
                stage,
                out_dir=self.work_dir / "stage_out",
                cwd=self.config.base_dir,
                private=private,
                required_kpis=(self.config.evaluate.score.objective,),
                cache=self.cache,
                race=self._race_for(stage, admitted, private=private),
            )
            if admitted
            else {}
        )
        for candidate in admitted:
            self._normalise(stage, candidate, outcomes[candidate.id], private=private)
            if not private:
                self._note_incumbent(stage, candidate, outcomes[candidate.id])
        return outcomes

    # -- race ------------------------------------------------------------

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _race_for(self, stage: StageConfig, candidates: Sequence[Candidate], *, private: bool) -> Race | None:
        held = self.incumbent.get(stage.id)
        if stage.race is None or private or not held:
            return None  # nothing to race against yet: the first candidate through always finishes
        return Race(
            objective=self.config.evaluate.score.objective,
            minimize=self.config.evaluate.score.direction == "minimize",
            incumbent={str(k): float(v) for k, v in held["values"].items()},
            after=stage.race.after,
            margin_pct=stage.race.margin_pct,
            exempt=frozenset(c.id for c in candidates if c.operator == SEED_OPERATOR),
        )

    def _note_incumbent(self, stage: StageConfig, candidate: Candidate, outcome: StageOutcome) -> None:
        """Remember the best candidate to have finished this stage, instance by instance."""
        if stage.race is None or not outcome.ok:
            return
        objective = self.config.evaluate.score.objective
        values = dict(zip(outcome.instance_names, outcome.vector_kpis.get(per_instance_key(objective), [])))
        score = outcome.kpis.get(objective)
        if not values or score is None:
            return
        held = self.incumbent.get(stage.id)
        better = held is None or (
            score < held["score"] if self.config.evaluate.score.direction == "minimize" else score > held["score"]
        )
        if better:
            self.incumbent[stage.id] = {"id": candidate.id, "score": score, "values": values}
            # Atomically: half a file reads back as "nothing to race against",
            # and a resumed run would quietly stop racing.
            _atomic_write(self.work_dir / "incumbent.json", json.dumps(self.incumbent, indent=2, sort_keys=True) + "\n")

    # -- normalize: baseline ---------------------------------------------

    @property
    def _reference_path(self) -> Path:
        return self.work_dir / "reference.json"

    def _load_reference(self) -> dict[str, dict[str, float]]:
        try:
            payload = json.loads(self._reference_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return {
            str(stage): {str(name): float(value) for name, value in values.items()}
            for stage, values in payload.items()
            if isinstance(values, dict)
        }

    def _normalise(
        self, stage: StageConfig, candidate: Candidate, outcome: StageOutcome, *, private: bool
    ) -> None:
        """Restate the objective as a percentage of the baseline, instance by instance.

        The plain mean over instances is a mean over *scales*: an instance with
        ten times the cost of the others decides the search on its own, and a
        two-percent gain everywhere else is rounding error. Dividing each
        instance by what the baseline reached on it gives every instance the
        same say. The divisor is fixed for the whole run -- it is a weight, not
        a measurement -- so a lucky or unlucky baseline run shifts where 100
        lies but never which of two candidates is ahead.

        The baseline is the seed candidate; its values become the reference
        the first time it passes the stage. The plain mean stays available as
        `<objective>_raw`.
        """
        if stage.normalize != "baseline" or not outcome.ok:
            return
        objective = self.config.evaluate.score.objective
        values = dict(zip(outcome.instance_names, outcome.vector_kpis[per_instance_key(objective)]))
        key = f"{stage.id}/private" if private else stage.id
        if candidate.operator == SEED_OPERATOR and key not in self.reference:
            unusable = [name for name, value in values.items() if not (value > 0 and math.isfinite(value))]
            if unusable:
                outcome.ok = False
                outcome.failure = (
                    f"cannot normalise against the baseline: its {objective} on instance "
                    f"{unusable[0]} is {values[unusable[0]]:g}, and a percentage of that means "
                    f"nothing. Set `normalize: none` on stage {stage.id!r} to use the plain mean"
                )
                return
            self.reference[key] = values
            self._reference_path.write_text(
                json.dumps(self.reference, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
                newline="\n",
            )
        reference = self.reference.get(key, {})
        shared = [name for name in outcome.instance_names if name in reference]
        if not shared:
            return  # no baseline has passed this stage (a cascade used on its own): the plain mean
        relative = [100.0 * values[name] / reference[name] for name in shared]
        outcome.kpis[f"{objective}_raw"] = outcome.kpis[objective]
        outcome.kpis[objective] = fmean(relative)
        outcome.vector_kpis[f"{objective}_pct_of_baseline"] = relative

    def _observer(
        self, candidate_id: str, stage: StageConfig, *, private: bool
    ) -> Callable[..., None] | None:
        """What `run_command_stage` reports to, with the context it lacks.

        The stage runner knows a seed and a duration; which candidate that was,
        at which stage, on the public inputs or the hold-out, is known here.
        Log paths are rewritten relative to the run directory so the event log
        still makes sense after the directory has been moved or copied.
        """
        if self.on_event is None:
            return None
        run_dir = self.work_dir.parent

        def relative(path: str) -> str:
            try:
                return Path(path).relative_to(run_dir).as_posix()
            except ValueError:
                return path

        def observe(type: str, **fields: Any) -> None:
            for key in ("stdout_log", "stderr_log"):
                if fields.get(key):
                    fields[key] = relative(fields[key])
            self.on_event(
                type, candidate_id=candidate_id, stage=stage.id, private=private, **fields
            )

        return observe

    def _note_behaviour(
        self,
        result: EvalResult,
        outcome: StageOutcome,
        stage: StageConfig,
        candidate_id: str,
    ) -> bool:
        """Fingerprint what the candidate did at `stage`. True == twin.

        The static stage is skipped on purpose: it reports size, not
        behaviour, and every syntactically valid candidate would look alike.
        `search.novelty.behavioural` can skip a stage too: `off` always does,
        `auto` does on a stochastic stage, where this filter is proven inert.
        """
        if stage.kind != "command" or outcome.private:
            return False
        mode = self.config.search.novelty.behavioural
        if mode == "off" or (mode == "auto" and stage.stochastic):
            return False
        signature = behaviour_signature(
            outcome.kpis,
            outcome.vector_kpis,
            ignore=self.config.evaluate.signature_ignore,
            # Coarser on a stochastic stage: see
            # `config.DEFAULT_SIGNATURE_DIGITS_STOCHASTIC`. At nine digits a
            # noisy evaluator makes every candidate look novel, which turns
            # the behavioural filter off exactly where it is most expensive to
            # be without.
            digits=self.config.evaluate.digits_for(stage),
        )
        if not signature:
            return False
        result.behaviour_signatures[stage.id] = signature
        verdict = self.signatures.check(stage.id, signature)
        if not verdict.duplicate or verdict.twin_id == candidate_id:
            self.signatures.add(candidate_id, stage.id, signature)
            return False
        result.behaviour_twin_id = verdict.twin_id
        result.rejected = True
        result.reject_reason = verdict.reason
        return True

    def _is_final(self, stage: StageConfig) -> bool:
        return stage.id == self.config.final_stage.id

    def _final_stage_blocked(self, stage: StageConfig) -> bool:
        if self.budget is None or not self._is_final(stage):
            return False
        return not self.budget.check_full_eval().allowed

    def _absorb(
        self, result: EvalResult, outcome: StageOutcome, stage: StageConfig
    ) -> None:
        result.outcomes.append(outcome)
        if outcome.raced_out:
            # Not a failure: the candidate keeps the score of the stage before,
            # does not reach this one, and so does not compete.
            result.raced_out = outcome.raced_out
            return
        if not outcome.ok:
            result.last_failure = _artefact(outcome)
            if stage.kind == "builtin-static":
                result.rejected = True
                result.reject_reason = outcome.failure
            result.score = self.config.evaluate.failure_score
            result.ranking_score = result.score
            return

        result.stages_reached.append(stage.id)
        if outcome.params is not None:
            result.params = dict(outcome.params)
        result.kpis.update(outcome.kpis)
        result.kpi_cv.update(outcome.kpi_cv)
        if outcome.text_feedback:
            result.feedback[stage.id] = outcome.text_feedback
        if stage.kind == "builtin-static":
            # No objective KPI here; a static pass carries no score of its own.
            return
        score, penalty_total, terms = compute_score(
            result.kpis, self.config.evaluate.score, self.config.evaluate.penalties
        )
        result.score = score
        result.penalty_total = penalty_total
        result.penalty_terms = terms
        result.stage_scores[stage.id] = score
        result.public_score = score
        result.ranking_score = ranking_score(
            score, score, result.private_score, self.config.evaluate.holdout_penalty
        )

    def _run_private(
        self,
        stage: StageConfig,
        by_id: dict[str, Candidate],
        paths: dict[str, Path],
        results: dict[str, EvalResult],
        survivors: Sequence[str],
    ) -> None:
        """Score the hold-out and record the gap. Never feeds the search signal."""
        if stage.kind != "command" or not (stage.private_inputs or stage.private_instances):
            return
        if not survivors:
            return
        began, failed = self._stage_started(stage, survivors, private=True), 0
        held_out = (
            self._run_instances(stage, [by_id[cid] for cid in survivors], paths, private=True)
            if stage.private_instances
            else None
        )
        for cid in survivors:
            outcome = (
                held_out[cid]
                if held_out is not None
                else run_command_stage(
                    paths[cid],
                    stage,
                    inputs=stage.private_inputs,
                    out_path=self.work_dir / "stage_out" / f"{cid}.{stage.id}.private.json",
                    cwd=self.config.base_dir,
                    private=True,
                    required_kpis=(self.config.evaluate.score.objective,),
                    observer=self._observer(cid, stage, private=True),
                    configuration=self._configurations.get(cid),
                    cache=self.cache,
                )
            )
            result = results[cid]
            result.outcomes.append(outcome)
            if not outcome.ok:
                failed += 1
                result.last_failure = _artefact(outcome)
                continue
            if outcome.text_feedback:
                # Appended to the public stage's entry rather than keyed apart:
                # one note per stage keeps the prompt's "latest feedback" rule a
                # one-liner, and the hold-out's remark is only worth reading
                # next to the public one it should be compared against.
                result.feedback[stage.id] = _merge_feedback(
                    result.feedback.get(stage.id, ""),
                    f"hold-out set: {outcome.text_feedback}",
                )
            merged = dict(result.kpis)
            merged.update(outcome.kpis)
            private_score, _, _ = compute_score(
                merged, self.config.evaluate.score, self.config.evaluate.penalties
            )
            result.private_score = private_score
            if result.public_score is not None:
                result.generalization_gap = result.public_score - private_score
            result.ranking_score = ranking_score(
                result.score,
                result.public_score,
                private_score,
                self.config.evaluate.holdout_penalty,
            )
        self._stage_finished(stage, began, len(survivors), failed, private=True)


def _merge_feedback(existing: str, addition: str) -> str:
    """Join two stage notes, still bounded by the framework's own limit."""
    joined = f"{existing}\n{addition}" if existing else addition
    return joined[:FEEDBACK_LIMIT]


def _artefact(outcome: StageOutcome) -> str:
    """One compact failure artefact for the next prompt."""
    parts = [f"stage {outcome.stage_id}: {outcome.failure or 'failed'}"]
    if outcome.stderr.strip():
        parts.append(outcome.stderr.strip()[-STDERR_LIMIT:])
    return "\n".join(parts)
