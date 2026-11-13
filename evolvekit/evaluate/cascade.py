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
import threading
import time
from dataclasses import replace
from pathlib import Path
from statistics import fmean, quantiles
from typing import TYPE_CHECKING, Any, Callable, Iterable, Mapping, Sequence

from evolvekit.budget import BudgetGuard
from evolvekit.candidate import SEED_OPERATOR, Candidate
from evolvekit.config import DEFAULT_SIGNATURE_DIGITS, Config, PromoteRule, StageConfig
from evolvekit.evaluate.cache import EvalCache
from evolvekit.evaluate.fanout import Job, Race, per_instance_key, race_looks, run_instance_stage
from evolvekit.evaluate.levels import Anchor, level_score, make_anchor
from evolvekit.evaluate.noise import (
    NOISE_FILE,
    Noise,
    about_pct,
    check_seeds,
    check_stage,
    measurable,
    record_check,
    run_duration,
)
from evolvekit.evaluate.scoring import compute_score, ranking_score
from evolvekit.evaluate.signature import (
    Behaviour,
    BehaviourIndex,
    behaviour_signature,
    compared_numbers,
    round_significant,
    twin_margins,
)
from evolvekit.evaluate.stages import (
    FEEDBACK_LIMIT,
    STDERR_LIMIT,
    Configuration,
    on_tree,
    run_build_stage,
    run_command_stage,
    run_static_stage,
    run_tree_static_stage,
)
from evolvekit.evaluate.types import EvalResult, StageOutcome
from evolvekit.leaderboard import veto_reason
from evolvekit.ledger import _atomic_write

if TYPE_CHECKING:  # `evolvekit.tree` is imported only for a tree problem
    from evolvekit.tree import Repo, Tree

__all__ = [
    "Cascade",
    "select_promoted",
    "archive_threshold",
    "finished_final_stage",
    "holdout_veto",
    "race_warnings",
]


def holdout_veto(
    config: Config, private_score: float | None, seed_private: float | None, margin: float = 0.0
) -> str | None:
    """Why a candidate's hold-out keeps it from competing, or `None`.

    With `evaluate.holdout_veto` on, a candidate whose private score is below
    the seed's private score by more than `margin` does not compete:
    `private < seed_private - margin`. The margin is the hold-out's noise
    (`Cascade.holdout_margin`); at 0 -- no noise estimate -- the rule is the
    literal comparison. Without a seed hold-out to compare with (the seed's
    own hold-out run failed, or there is none), nobody is vetoed."""
    if not config.evaluate.holdout_veto or private_score is None or seed_private is None:
        return None
    if private_score < seed_private - margin:
        return veto_reason(seed_private, margin)
    return None


def finished_final_stage(
    config: Config,
    *,
    rejected: bool,
    last_failure: str | None,
    stages_reached: Sequence[str],
    private_score: float | None,
    gated: str | None = None,
    seed_private: float | None = None,
    holdout_margin: float = 0.0,
) -> bool:
    """Whether a candidate's score may be compared with anybody else's.

    A score means something only next to scores from the same stage on the
    same inputs, so a candidate competes -- is ranked, archived, bred from --
    only when it got all the way through, and broke no gate (`evaluate.gates`):
    a gated candidate's score is real, but it is the score of a plan nobody
    would use. That rules out four cases which used to be ranked as if they
    had:

    * a **failed** evaluation. It carries `evaluate.failure_score`, and the
      default `-1000` outranks every healthy candidate whose minimised cost is
      above 1000;
    * a candidate **not promoted** past a cheaper stage, whose score comes from
      a different input set;
    * a final stage **skipped** by `budget.max_full_evals_per_day`;
    * a **hold-out run that failed**, which would otherwise keep its public
      score with no discount -- the one thing the hold-out is there to prevent.

    And one case that finished: a hold-out **below the seed's**
    (`seed_private`) by more than `holdout_margin`, when `evaluate.holdout_veto`
    is on; see `holdout_veto`.

    Takes plain values rather than an `EvalResult` so that a `runs.jsonl` row
    written before the `competes` field existed can be judged by the same rule.
    """
    if rejected or last_failure or gated:
        return False
    final = config.final_stage
    if final.id not in stages_reached:
        return False
    held_out = final.private_inputs or final.private_instances
    if final.kind == "command" and held_out and private_score is None:
        return False
    return holdout_veto(config, private_score, seed_private, holdout_margin) is None


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
    *,
    band: float = 0.0,
) -> list[str]:
    """Apply one promote rule to `(candidate_id, score)` pairs.

    An empty rule promotes everything. When both sub-rules are set a candidate
    only needs to satisfy one, matching the proposal's `top_k … or score >= p`.

    `band` is the stage's noise (twice one candidate's score sd): with
    `top_k_per_generation = k`, every candidate within `band` of the k-th
    score is promoted beside the top k, the two together capped at `2k` by
    score (ties by id). The `archive_percentile` union is outside the cap.
    A band of 0 -- no noise estimate -- is the top k exactly.
    """
    ids = [cid for cid, _ in scored]
    if rule.top_k_per_generation is None and rule.archive_percentile is None:
        return ids

    chosen: set[str] = set()
    k = rule.top_k_per_generation
    if k is not None:
        ranked = sorted(scored, key=lambda item: (-item[1], item[0]))
        chosen.update(cid for cid, _ in ranked[:k])
        if k > 0 and len(ranked) > k and math.isfinite(band) and band > 0:
            floor = ranked[k - 1][1] - band
            chosen.update(cid for cid, score in ranked[: 2 * k] if score >= floor)
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
        stop: threading.Event | None = None,
        time_left: Callable[[], float | None] | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.config = config
        self.log = log
        """A console line for the run (the driver's `log`); `None` says nothing."""
        self.stop = stop
        """The run's own stop (`evolvekit/stopping.py`): once set, evaluations
        in flight are stopped, none is started, and what did not finish is
        abandoned rather than failed."""
        self.time_left = time_left
        """The run's time left in seconds (`None`: no time budget): what a
        build may take at most, beside `problem.tree.build_timeout_s`."""
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
        self.anchors: dict[str, Anchor] = {
            key: Anchor.from_json(value)
            for key, value in self._load_json(self.work_dir / "levels.json").items()
            if isinstance(value, dict)
        }
        """Per stage, what `evaluate.score.levels` are measured from: the
        seed's values (`evaluate/levels.py`). On disk for the same reason."""
        self.noise: Noise = Noise.load(self.work_dir / NOISE_FILE)
        """The run's noise estimate (`evaluate/noise.py`): every command-stage
        outcome adds to it, and it is saved after every generation. A run from
        before the file starts with none."""
        self.seed_private: float | None = None
        """The seed's private score: what `evaluate.holdout_veto` compares every
        hold-out with. Set whenever a seed is evaluated (the last one wins: an
        aborted seed is evaluated again), `None` when its hold-out did not
        score; a resumed run rebuilds it from `runs.jsonl`
        (`leaderboard.seed_private`)."""
        self.seed_public: float | None = None
        """The same seed's public score, set and rebuilt with `seed_private`:
        what `holdout_margin` rescales the public run's noise by."""
        self.promote_k: dict[str, int] = {}
        """Per stage, the `top_k_per_generation` the run has moved it to
        (`search/screening.py`, a stage with `promote.adapt`); a stage not
        here promotes its configured k."""
        self.skipped: set[str] = set()
        """Stages passed over (a screen switched off): their candidates go
        straight to the next stage, with no score of their own there."""
        self._trees: dict[str, Tree] = {}
        """Per candidate, its worktree (`problem.tree`): made by `_materialise`."""
        self._tree_errors: dict[str, str] = {}
        """Per candidate whose tree could not be made, why: the static stage
        refuses it with that sentence."""
        self.repo: Repo | None = None
        if config.problem.tree is not None:
            from evolvekit import tree

            self.repo = tree.Repo(config.problem.tree.repo)

    # -- public ----------------------------------------------------------

    def evaluate_generation(
        self,
        candidates: Sequence[Candidate],
        archive_scores: Iterable[float] = (),
        *,
        stage_scores: Mapping[str, Sequence[float]] | None = None,
    ) -> dict[str, EvalResult]:
        """Evaluate a whole generation; returns results keyed by candidate id.

        A stage's `promote.archive_percentile` is read over `stage_scores[stage.id]`
        -- the archive members' own scores at that stage -- when it has any,
        else over `archive_scores` (their fitness: a caller from before stage
        scores were handed over). Its top k is widened by the stage's noise
        band (`select_promoted`), twice one candidate's score sd there."""
        prior = list(archive_scores)
        own = stage_scores or {}
        results: dict[str, EvalResult] = {
            c.id: EvalResult(score=self.config.evaluate.failure_score) for c in candidates
        }
        paths = {c.id: self._materialise(c) for c in candidates}
        for cid in paths:
            made = self._trees.get(cid)
            if made is not None:
                results[cid].commit = made.commit

        stages = self.config.evaluate.stages
        alive = [c.id for c in candidates]
        by_id = {c.id: c for c in candidates}

        for index, stage in enumerate(stages):
            if not alive:
                break
            if self._stopping():
                # The run's time limit, or a stop request: what has not
                # finished is abandoned, not failed, and nothing more starts.
                for cid in alive:
                    results[cid].abandoned = "the run was stopped before this candidate finished"
                break
            if stage.id in self.skipped and index < len(stages) - 1:
                continue  # a screen switched off: everyone goes on to the next stage
            survivors: list[str] = []
            began = self._stage_started(stage, alive, private=False)
            outcomes = (
                self._run_instances(stage, [by_id[cid] for cid in alive], paths)
                if stage.fans_out
                else None
            )
            failed = raced_out = abandoned = 0
            for cid in alive:
                outcome = (
                    outcomes[cid]
                    if outcomes is not None
                    else self._run_stage(stage, by_id[cid], paths[cid])
                )
                # Raced out is not failed: nothing went wrong, the candidate
                # was stopped because it was behind. Neither is abandoned: the
                # run was stopped.
                if outcome.raced_out:
                    raced_out += 1
                elif outcome.abandoned:
                    abandoned += 1
                elif not outcome.ok:
                    failed += 1
                self._absorb(results[cid], outcome, stage)
                self._note_noise(stage, by_id[cid], outcome, private=False)
                if outcome.params is not None:
                    self._configure(cid, paths[cid], outcome.params)
                if not outcome.ok:
                    continue
                # A candidate that behaved exactly like one already evaluated
                # at this stage keeps its score, but buys nothing further: no
                # deeper stage, no hold-out run, no archive entry.
                if self._note_behaviour(results[cid], outcome, stage, cid, generation=by_id[cid].generation):
                    continue
                # So does one that broke a gate: not a failure, not a contender.
                if self._gate(results[cid], outcome, stage):
                    continue
                survivors.append(cid)

            self._stage_finished(
                stage, began, len(alive), failed, private=False, raced_out=raced_out, abandoned=abandoned
            )
            is_final = index == len(stages) - 1
            if is_final:
                self._run_private(stage, by_id, paths, results, survivors)
                break

            scored = [(cid, results[cid].score) for cid in survivors]
            members = list(own.get(stage.id) or ()) or prior
            band = 2.0 * self.noise.score_sd(stage.id, self.config.evaluate.score, stage.seeds)
            rule = stage.promote
            if stage.id in self.promote_k and rule.top_k_per_generation is not None:
                rule = replace(rule, top_k_per_generation=self.promote_k[stage.id])
            alive = select_promoted(scored, rule, members, band=band)

        for cid, candidate in by_id.items():
            # The last seed evaluated is the reference, as `leaderboard.seed_private`
            # reads it back on a resume: one whose hold-out failed leaves none.
            if candidate.operator == SEED_OPERATOR and not results[cid].abandoned:
                self.seed_private = results[cid].private_score
                self.seed_public = results[cid].public_score
                self._check_noise(candidate, paths, results)
                self.noise.save()

        # One margin for the whole generation, from the estimate as it stands
        # once this generation's hold-out runs and the seed's are in.
        margin = self.holdout_margin()
        for result in results.values():
            # `finished_final_stage(..., seed_private=...)` in two halves, so
            # that a vetoed candidate's row can say why it does not compete.
            finished = not result.abandoned and finished_final_stage(
                self.config,
                rejected=result.rejected,
                last_failure=result.last_failure,
                stages_reached=result.stages_reached,
                private_score=result.private_score,
                gated=result.gated,
            )
            judged = finished and result.private_score is not None
            result.holdout_margin = margin if judged else None
            result.veto = (
                holdout_veto(self.config, result.private_score, self.seed_private, margin) if finished else None
            )
            result.competes = finished and result.veto is None
        self.noise.save()
        return results

    def _stopping(self) -> bool:
        return self.stop is not None and self.stop.is_set()

    def reevaluate(
        self, candidate: Candidate, seed: int, *, earlier_kpis: Mapping[str, float] | None = None
    ) -> StageOutcome | None:
        """Run `candidate` once more at the final stage, public inputs only, on
        `seed` (`search.reevaluate_best_every`): normalised against the run's
        reference and scored as `_absorb` scores -- over `earlier_kpis` (the
        candidate's measures as first written, never its pooled ones; its own
        `kpis` without them) with this run's on top -- the score of this run
        alone in `outcome.score`. No generation's results, no race, no incumbent, no
        behaviour index and no noise entry are touched (the driver pools the
        run, `Noise.pool`); no `eval_*` events (it is not a candidate of the
        search), and the cache is used (a resumed run re-reads a run it made).
        Counted against `budget.max_full_evals_per_day` when it starts. `None`
        when it does not start: the cap says no, or the run is stopping."""
        final = self.config.final_stage
        if final.kind != "command" or self._stopping():
            return None
        if self.budget is not None:
            if not self.budget.check_full_eval().allowed:
                return None
            self.budget.record_full_eval()
        path = self._materialise(candidate)
        space = self.config.problem.parameters
        if candidate.params is not None and space is not None and candidate.id not in self._configurations:
            self._configure(candidate.id, path, dict(candidate.params))  # a resumed run's best
        out = self.work_dir / "stage_out" / "reevaluate"
        if final.fans_out:
            job = Job(candidate_id=candidate.id, candidate_path=path,
                      configuration=self._configurations.get(candidate.id), **self._on_tree(candidate.id))
            outcome = run_instance_stage(
                [job], final, out_dir=out, cwd=self.config.base_dir,
                required_kpis=self.config.evaluate.required_kpis, cache=self.cache, seeds=(seed,), stop=self.stop,
            )[candidate.id]
        else:
            outcome = run_command_stage(
                path, final, inputs=final.inputs, out_path=out / f"{candidate.id}.{final.id}.{seed}.json",
                cwd=self.config.base_dir, required_kpis=self.config.evaluate.required_kpis,
                configuration=self._configurations.get(candidate.id), cache=self.cache, cancel=self.stop,
                seeds=(seed,), **self._on_tree(candidate.id),
            )
        if not outcome.ok or outcome.abandoned:
            return outcome
        if final.fans_out:  # as `_run_instances`: only a per-instance stage is normalised
            self._normalise(final, candidate, outcome, private=False)
            if not outcome.ok:
                return outcome
        written = candidate.kpis if earlier_kpis is None else earlier_kpis
        kpis = {**written, **outcome.kpis}  # as `_absorb` scores: earlier stages' measures too
        if self.config.evaluate.score.levels:
            outcome.score = self._level_score(final.id, kpis, outcome.vector_kpis)
        else:
            outcome.score, _, _ = compute_score(kpis, self.config.evaluate.score, self.config.evaluate.penalties)
        return outcome

    # -- levels ----------------------------------------------------------

    def _level_score(
        self, key: str, values: dict[str, float], vectors: Mapping[str, Sequence[float]] | None = None
    ) -> float:
        """`evaluate.score.levels` as one score, measured from the anchor of
        stage `key`. The first candidate through a stage anchors it -- in a
        run that is the seed, which therefore scores exactly 0 -- and the
        anchor is kept on disk so that a resumed run measures from it too.
        `vectors` is the stage's `vector_kpis`: on a stage that runs per
        instance, the last level is scaled by the seed's range over them."""
        levels = self.config.evaluate.score.levels
        anchor = self.anchors.get(key)
        if anchor is None:
            last = levels[-1].kpi
            spread = (vectors or {}).get(per_instance_key(last))
            anchor = self.anchors[key] = make_anchor(levels, values, {last: spread} if spread else None)
            # Atomically: half a file reads back as "no anchor", and a resumed
            # run would quietly anchor at its first child instead of the seed.
            _atomic_write(
                self.work_dir / "levels.json",
                json.dumps({k: a.to_json() for k, a in self.anchors.items()}, indent=2, sort_keys=True) + "\n",
            )
        return level_score(levels, anchor, values)

    # -- gates -----------------------------------------------------------

    def _broken_gate(self, kpis: dict[str, float]) -> str | None:
        """The first gate `kpis` break, as `"missed = 2 > 0"`, or `None`."""
        for gate in self.config.evaluate.gates:
            broken = gate.broken_by(kpis)
            if broken is not None:
                return broken
        return None

    def _gate(self, result: EvalResult, outcome: StageOutcome, stage: StageConfig, *, private: bool = False) -> bool:
        """Judge a command stage's aggregated KPIs against `evaluate.gates`.
        True when the candidate broke one: it stops here, with the reason."""
        if stage.kind != "command":
            return False
        broken = self._broken_gate(outcome.kpis)
        if broken is None:
            return False
        result.gated = f"{broken} on the hold-out" if private else broken
        return True

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
        if self.repo is not None:
            return self._materialise_tree(candidate)
        path = self.candidates_dir / f"{candidate.id}.py"
        path.write_text(candidate.source, encoding="utf-8", newline="\n")
        return path

    def _materialise_tree(self, candidate: Candidate) -> Path:
        """The candidate's diff as a worktree with a commit; its folder is `{tree}`.

        A diff that does not apply makes no tree: the static stage refuses the
        candidate with the reason, and the path returned does not exist."""
        from evolvekit import tree

        assert self.repo is not None and self.config.problem.tree is not None
        cid = candidate.id
        self._trees.pop(cid, None)
        self._tree_errors.pop(cid, None)
        try:
            made = tree.materialise(
                self.work_dir.parent, repo=self.repo, base=self.config.problem.tree.base,
                diff=candidate.block, candidate_id=cid,
            )
        except tree.TreeError as exc:
            self._tree_errors[cid] = str(exc)
            folder = tree.tree_dir(self.work_dir.parent, cid)
            return folder / self.repo.prefix if self.repo.prefix else folder
        self._trees[cid] = made
        return made.path

    def _on_tree(self, candidate_id: str) -> dict[str, str | None]:
        """`app` and `source_id` for a command stage on the candidate's tree
        (`on_tree`); nothing for a skeleton or parameters problem."""
        return on_tree(self.config.problem, self._trees.get(candidate_id))

    def _run_stage(
        self, stage: StageConfig, candidate: Candidate, path: Path
    ) -> StageOutcome:
        if stage.kind == "builtin-build":
            # Only a candidate whose tree was made gets here: the static stage
            # refused the others.
            return run_build_stage(
                self._trees[candidate.id], stage, self.config.problem, cancel=self.stop,
                time_left_s=self.time_left() if self.time_left is not None else None,
            )
        if stage.kind == "builtin-static" and self.repo is not None:
            return run_tree_static_stage(
                path, candidate.block, self._tree_errors.get(candidate.id), stage, self.config.problem,
                prefix=self.repo.prefix,
            )
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
            required_kpis=self.config.evaluate.required_kpis,
            observer=self._observer(candidate.id, stage, private=False),
            configuration=self._configurations.get(candidate.id),
            cache=self.cache,
            cancel=self.stop,
            **self._on_tree(candidate.id),
        )
        if self._is_final(stage) and self.budget is not None and not outcome.abandoned:
            self.budget.record_full_eval()
        return outcome

    def _stage_started(self, stage: StageConfig, candidates: Sequence[str], *, private: bool) -> float:
        """Tell the event log how much work a stage is about to be for this
        generation -- a long stage is otherwise a progress bar with no length."""
        if self.on_event is not None and stage.kind == "command":
            instances = stage.private_instances if private else stage.instances
            extra: dict[str, Any] = {}
            if stage.race is not None and not private:
                # The race's own n (`Race.population`): the instances the
                # incumbent is above 0 on, once there is one.
                held = (self.incumbent.get(stage.id) or {}).get("values") or {}
                n = sum(1 for v in held.values() if float(v) > 0) if held else len(instances)
                extra["race_looks"] = list(race_looks(stage.race.after, min(n, len(instances))))
            self.on_event(
                "stage_started",
                stage=stage.id,
                private=private,
                candidates=list(candidates),
                runs_per_candidate=max(1, len(instances)) * stage.seeds,
                workers=stage.workers,
                **extra,
            )
        return time.perf_counter()

    def _stage_finished(
        self, stage: StageConfig, began: float, candidates: int, failed: int, *, private: bool,
        raced_out: int = 0, abandoned: int = 0,
    ) -> None:
        if self.on_event is not None and stage.kind == "command":
            self.on_event(
                "stage_finished",
                stage=stage.id,
                private=private,
                candidates=candidates,
                failed=failed,
                raced_out=raced_out,
                abandoned=abandoned,
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
                        **self._on_tree(c.id),
                    )
                    for c in admitted
                ],
                stage,
                out_dir=self.work_dir / "stage_out",
                cwd=self.config.base_dir,
                private=private,
                required_kpis=self.config.evaluate.required_kpis,
                cache=self.cache,
                race=self._race_for(stage, admitted, private=private),
                stop=self.stop,
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
        objective = self.config.evaluate.score.objective
        return Race(
            objective=objective,
            minimize=self.config.evaluate.score.direction == "minimize",
            incumbent={str(k): float(v) for k, v in held["values"].items()},
            after=stage.race.after,
            margin_pct=stage.race.margin_pct,
            exempt=frozenset(c.id for c in candidates if c.operator == SEED_OPERATOR),
            # Raw per-instance values on both sides (decision 4); a ratio, so
            # raw and percent of the seed agree.
            cv=self.noise.vector_cv(stage.id, per_instance_key(objective)),
            instances=len(stage.instances),
        )

    def _note_incumbent(self, stage: StageConfig, candidate: Candidate, outcome: StageOutcome) -> None:
        """Remember the best candidate to have finished this stage, instance by
        instance. Never one that broke a gate: it is not a contender, so it is
        not the one to race against either."""
        if stage.race is None or not outcome.ok or self._broken_gate(outcome.kpis) is not None:
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
        pct = f"{objective}_pct_of_baseline"
        outcome.vector_kpis[pct] = relative
        # The noise in the units each number is in: `<obj>_per_instance` stays
        # raw (the race and the twin rule compare raw values), the percent
        # vector's is scaled by (100 / ref_i)², and the stage-level objective
        # -- now a mean of M' percentages -- has the variance of one run
        # fmean(pct variances) / M'; `<obj>_raw` keeps the raw one.
        position = {name: i for i, name in enumerate(outcome.instance_names)}
        raw_var = outcome.vector_var.get(per_instance_key(objective))
        if raw_var is not None and len(raw_var) == len(outcome.instance_names):
            pct_var = [(100.0 / reference[name]) ** 2 * raw_var[position[name]] for name in shared]
            outcome.vector_var[pct] = pct_var
            outcome.kpi_var[f"{objective}_raw"] = fmean(raw_var) / len(raw_var)
            outcome.kpi_var[objective] = fmean(pct_var) / len(pct_var)
        rows = outcome.seed_vectors.get(per_instance_key(objective))
        if rows and all(len(row) == len(outcome.instance_names) for row in rows):
            # Per seed in percent too, so that paired differences exist in the score's unit.
            pct_rows = [[100.0 * row[position[name]] / reference[name] for name in shared] for row in rows]
            outcome.seed_vectors[pct] = pct_rows
            if objective in outcome.seed_runs:
                outcome.seed_runs[f"{objective}_raw"] = outcome.seed_runs[objective]
            outcome.seed_runs[objective] = [fmean(row) for row in pct_rows]

    # -- noise -----------------------------------------------------------

    def holdout_margin(self) -> float:
        """How far below the seed's hold-out score a candidate's may lie before
        the veto keeps it out: `2 · σ_priv`, twice the sd of one candidate's
        hold-out score (`Noise.score_sd`, a mean over the final stage's
        `seeds`; the hold-out runs as many as the public stage). The
        difference of two equal candidates then has sd `√2 · σ_priv`, and an
        equal candidate is kept out with P(Z < -√2), about 8 %.

        `σ_priv` is the hold-out's own estimate when it has one -- on a
        repeatable hold-out, a paired one (`Noise.read_known`). Otherwise
        (a hold-out that replicates nothing, which calibration does not
        cover) the public run's noise, rescaled to the hold-out's size by
        `|seed_private / seed_public|` -- only when both seed scores are
        non-zero and of one sign: across a change of sign the ratio says
        nothing about scale. Else 0, the literal rule; always 0 with
        `score.levels`. On a repeatable stage both estimates are the paired
        ones, 0 at `seeds: 1`."""
        final, score = self.config.final_stage, self.config.evaluate.score
        if score.levels:
            return 0.0
        private_key = f"{final.id}/private"
        if self.noise.read_known(private_key):
            return 2.0 * self.noise.score_sd(private_key, score, final.seeds)
        public, private = self.seed_public, self.seed_private
        if public is None or private is None or public == 0 or private == 0 or (public > 0) != (private > 0):
            return 0.0
        return 2.0 * self.noise.score_sd(final.id, score, final.seeds) * abs(private / public)

    def _note_noise(
        self, stage: StageConfig, candidate: Candidate, outcome: StageOutcome, *, private: bool
    ) -> None:
        """Add a command stage's outcome to the run's noise estimate."""
        if stage.kind != "command":
            return
        key = f"{stage.id}/private" if private else stage.id
        self.noise.observe(key, candidate.id, outcome, is_seed=candidate.operator == SEED_OPERATOR)

    def _check_noise(self, seed: Candidate, paths: dict[str, Path], results: dict[str, EvalResult]) -> None:
        """The seed run again at the final stage, once per run (`evaluate/noise.py`):
        seed 0 once more with the cache bypassed -- a lookup would read the
        first run back and call every evaluator repeatable -- and, at
        `seeds: 1`, seed 1, on the instance whose seed run took the median
        time. It says whether the stage is repeatable and, where nothing
        replicates, calibrates the noise. Not counted against
        `budget.max_full_evals_per_day`: it is a measurement, not a candidate."""
        final = self.config.final_stage
        if final.kind != "command" or self.noise.repeatable(final.id) is not None or self._stopping():
            return
        result = results[seed.id]
        if result.abandoned or final.id not in result.stages_reached:
            return
        if not measurable(final):
            if final.seeds > 1:
                # Defensive: `config.py` refuses `seeds > 1` without `{seed}`, so
                # this is not reached through a config. Such runs would be
                # repeats of one command: whatever differs between them is noise.
                for key in (final.id, f"{final.id}/private"):
                    self.noise.set_repeatable(key, False)
            return  # seeds: 1 and no {seed}: nothing to measure, σ̂ = 0 as before
        first = next(
            (o for o in result.outcomes if o.stage_id == final.id and not o.private and o.ok), None
        )
        if first is None:
            return
        where = self._check_where(final, first)
        left = self.time_left() if self.time_left is not None else None
        needed = run_duration(final, first)
        if left is not None and left < needed:
            self._say(
                f"noise: the seed was not run again{where}: {left:.0f} s of the run's time is left, "
                f"less than one of its runs ({needed:.0f} s); the noise stays unmeasured"
            )
            return
        try:
            extra = self._run_again(final, seed, paths[seed.id], first)
        except Exception as exc:  # noqa: BLE001 - a measurement must not end the run
            self._say(f"noise: the seed could not be run again{where} ({exc}); the noise stays unmeasured")
            return
        check = None
        if extra.ok and not extra.abandoned:
            check = record_check(
                self.noise, final.id, final, first, extra,
                objective=self.config.evaluate.score.objective,
                digits=self.config.evaluate.signature_digits,
                ignore=self.config.evaluate.signature_ignore,
            )
        if check is None:
            why = extra.failure or ("it was stopped" if extra.abandoned else "its measures could not be compared")
            self._say(f"noise: the seed could not be run again{where} ({why}); the noise stays unmeasured")
            return
        cv = self.noise.cv(final.id, self.config.evaluate.score.objective)
        if self.on_event is not None:
            self.on_event("noise_checked", stage=final.id, instance=check.instance,
                          repeatable=check.repeatable, runs=check.runs, cv=cv)
        if check.repeatable:
            said = "the evaluator gives the same numbers for the same seed (comparisons are paired by seed)"
        else:
            said = f"one run varies by about {about_pct(cv)} % (the search's comparisons allow for it)"
        self._say(f"noise: the seed run again{where}: {said}")

    def _run_again(self, final: StageConfig, seed: Candidate, path: Path, first: StageOutcome) -> StageOutcome:
        """The extra runs of `_check_noise`: no cache, no observer (they are
        not evaluations of the search), no budget entry."""
        stage, seeds = check_stage(final, first), check_seeds(final)
        on_tree_args = self._on_tree(seed.id)
        if stage.fans_out:
            job = Job(candidate_id=seed.id, candidate_path=path,
                      configuration=self._configurations.get(seed.id), **on_tree_args)
            return run_instance_stage(
                [job], stage, out_dir=self.work_dir / "stage_out" / "noise-check", cwd=self.config.base_dir,
                required_kpis=self.config.evaluate.required_kpis, cache=None, seeds=seeds, stop=self.stop,
            )[seed.id]
        return run_command_stage(
            path, stage, inputs=stage.inputs,
            out_path=self.work_dir / "stage_out" / "noise-check" / f"{seed.id}.{stage.id}.json",
            cwd=self.config.base_dir, required_kpis=self.config.evaluate.required_kpis,
            configuration=self._configurations.get(seed.id), cache=None, cancel=self.stop,
            seeds=seeds, **on_tree_args,
        )

    @staticmethod
    def _check_where(stage: StageConfig, outcome: StageOutcome) -> str:
        picked = check_stage(stage, outcome)
        if picked is stage or not picked.instances:
            return ""
        index = stage.instances.index(picked.instances[0])
        names = outcome.instance_names or stage.instance_names()
        return f" on {names[index]}" if index < len(names) else ""

    def _say(self, line: str) -> None:
        if self.log is not None:
            self.log(line)

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
        *,
        generation: int | None = None,
    ) -> bool:
        """Fingerprint what the candidate did at `stage`. True == twin.

        The static stage is skipped on purpose: it reports size, not
        behaviour, and every syntactically valid candidate would look alike.
        `search.novelty.behavioural` can skip a stage too: `off` always does;
        `auto` does not check a stochastic stage while it knows neither a noise
        nor that the stage is repeatable, but keeps what it saw.

        Two rules (`evaluate/signature.py`): on a stage that is not repeatable
        and has a noise estimate (`noise.sd` of the objective > 0), the child
        is a twin of the best candidate of an earlier round (`generation`:
        never one of its own round, whose order of evaluation is arbitrary,
        Decision 15) it is within the noise of on every compared number,
        among those that scored at least as well. On a
        repeatable stage, and with no noise, the 9-digit hash. Either way the
        candidate's hash and numbers are kept, so the seed is a target in both.
        """
        if stage.kind != "command" or outcome.private:
            return False
        mode = self.config.search.novelty.behavioural
        if mode == "off":
            return False
        evaluate = self.config.evaluate
        objective = evaluate.score.objective
        repeatable = self.noise.repeatable(stage.id)
        noisy = repeatable is not True and self.noise.sd(stage.id, objective) > 0
        skip = mode == "auto" and stage.stochastic and repeatable is not True and not noisy
        signature = behaviour_signature(
            outcome.kpis,
            outcome.vector_kpis,
            ignore=evaluate.signature_ignore,
            digits=evaluate.signature_digits,
        )
        seen = compared_numbers(
            outcome, ignore=evaluate.signature_ignore, per_instance=stage.fans_out, objective=objective
        )
        digits = evaluate.signature_digits
        score = float(result.stage_scores.get(stage.id, result.score))
        # Compared as measured: `within` rounds (at `signature_digits`) only the
        # numbers it compares exactly. Kept at nine digits at least, whatever
        # `signature_digits` says, so that what a later child is compared with
        # -- live or after a resume -- is the number, not a coarse rounding of it.
        behaviour = Behaviour(seen.kpis, seen.vectors, score)
        keep = max(DEFAULT_SIGNATURE_DIGITS, digits)
        kept = Behaviour(
            {k: round_significant(v, keep) for k, v in seen.kpis.items()},
            {k: [round_significant(x, keep) for x in v] for k, v in seen.vectors.items()},
            score,
        )
        if not signature and not (behaviour.kpis or behaviour.vectors):
            return False
        # Nothing to compare number by number (every measure ignored but a
        # derived one): the hash, which then says no more than it did before.
        noisy = noisy and bool(behaviour.kpis or behaviour.vectors)
        if signature:
            result.behaviour_signatures[stage.id] = signature
        result.behaviour_values[stage.id] = kept.to_json()
        if skip:
            self.signatures.add(candidate_id, stage.id, signature, kept, generation=generation)
            return False
        if noisy:
            margins = twin_margins(
                behaviour,
                {k: self.noise.sd(stage.id, k) for k in behaviour.kpis},
                {
                    k: [self.noise.vector_sd(stage.id, k, i) for i in range(len(v))]
                    for k, v in behaviour.vectors.items()
                },
                stage.seeds,
                cvs={
                    **{k: self.noise.cv(stage.id, k) for k in behaviour.kpis},
                    **{k: self.noise.vector_cv(stage.id, k) for k in behaviour.vectors},
                },
            )
            # Earlier rounds within the noise; this round's only when every
            # number is the same as measured (`keep` digits: an exact duplicate
            # is a twin in any order, and `signature_digits` rounds nothing here).
            verdict = self.signatures.check_values(
                stage.id, behaviour, margins, digits, exclude=candidate_id, before=generation, exact_digits=keep
            )
        else:
            verdict = self.signatures.check(stage.id, signature)
        if not verdict.duplicate or verdict.twin_id == candidate_id:
            self.signatures.add(candidate_id, stage.id, signature, kept, generation=generation)
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
        if outcome.abandoned:
            # Not a failure either: the run was stopped. The driver records
            # nothing of this generation, and the resumed run finishes it.
            result.abandoned = outcome.failure or "the run was stopped before this candidate finished"
            return
        if outcome.raced_out:
            # Not a failure: the candidate keeps the score of the stage before,
            # does not reach this one, and so does not compete.
            result.raced_out = outcome.raced_out
            return
        if not outcome.ok:
            result.last_failure = _artefact(outcome)
            if stage.kind in ("builtin-static", "builtin-build"):
                result.rejected = True
                result.reject_reason = outcome.failure
            said = outcome.stderr if outcome.stderr.strip() else outcome.stdout
            if stage.kind == "builtin-build" and said.strip():
                # What the next prompt shows: the build's own last words
                # (a build tool that reports on stdout, when stderr is empty).
                result.feedback[stage.id] = said[-FEEDBACK_LIMIT:]
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
        if stage.kind in ("builtin-static", "builtin-build"):
            # No objective KPI here; a static pass or a build carries no score of its own.
            return
        if self.config.evaluate.score.levels:
            score, penalty_total, terms = self._level_score(stage.id, result.kpis, outcome.vector_kpis), 0.0, {}
        else:
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
        """Scores the hold-out, records the gap, and with `holdout_veto` keeps a
        candidate below the seed's hold-out from competing. With a non-zero
        `holdout_penalty` it also enters the ranking score."""
        if stage.kind != "command" or not (stage.private_inputs or stage.private_instances):
            return
        if not survivors:
            return
        if self._stopping():
            for cid in survivors:
                results[cid].abandoned = "the run was stopped before its hold-out run"
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
                    required_kpis=self.config.evaluate.required_kpis,
                    observer=self._observer(cid, stage, private=True),
                    configuration=self._configurations.get(cid),
                    cache=self.cache,
                    cancel=self.stop,
                    **self._on_tree(cid),
                )
            )
            result = results[cid]
            result.outcomes.append(outcome)
            self._note_noise(stage, by_id[cid], outcome, private=True)
            if outcome.abandoned:
                result.abandoned = outcome.failure or "the run was stopped during its hold-out run"
                continue
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
            # The hold-out's own measures only: a number the hold-out run did not
            # report is not filled in from the public run, it would be a score
            # about two different sets of cases. A penalty it did not report
            # counts as 0, as on the public run.
            score_cfg = self.config.evaluate.score
            needed = [level.kpi for level in score_cfg.levels] if score_cfg.levels else list(score_cfg.weights)
            missing = sorted(k for k in needed if k not in outcome.kpis)
            # With `levels` this cannot fire today: every level's measure is in
            # `required_kpis`, so a hold-out run without one has already failed
            # above. Kept for the weights, whose secondary measures are not
            # required, and in case that list ever changes.
            if missing:
                failed += 1
                result.last_failure = (
                    f"stage {stage.id}: the hold-out run did not report {', '.join(missing)}; "
                    "a hold-out score is computed from the hold-out run's own KPIs"
                )
                continue
            if score_cfg.levels:
                private_score = self._level_score(f"{stage.id}/private", outcome.kpis, outcome.vector_kpis)
            else:
                private_score, _, _ = compute_score(
                    outcome.kpis, score_cfg, self.config.evaluate.penalties
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
            self._gate(result, outcome, stage, private=True)
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
