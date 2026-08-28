"""The staged evaluator: cheap rungs for everyone, expensive rungs for the few.

`stage[i].promote` governs who moves from stage i to stage i+1. Stage 0 is
`builtin-static` and always promotes everyone it does not reject -- it has no
score to rank by, and letting a static pass masquerade as a good score would
put unevaluated candidates at the top of the archive.

A candidate keeps the score of the deepest stage it actually reached. Not being
promoted is not a punishment; failing a stage is (`evaluate.failure_score`).
"""

from __future__ import annotations

from pathlib import Path
from statistics import quantiles
from typing import Iterable, Sequence

from evolvekit.budget import BudgetGuard
from evolvekit.candidate import Candidate
from evolvekit.config import Config, PromoteRule, StageConfig
from evolvekit.evaluate.scoring import compute_score, ranking_score
from evolvekit.evaluate.signature import BehaviourIndex, behaviour_signature
from evolvekit.evaluate.stages import (
    FEEDBACK_LIMIT,
    STDERR_LIMIT,
    run_command_stage,
    run_static_stage,
)
from evolvekit.evaluate.types import EvalResult, StageOutcome

__all__ = ["Cascade", "select_promoted", "archive_threshold"]


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
    ) -> None:
        self.config = config
        self.work_dir = Path(work_dir)
        self.candidates_dir = self.work_dir / "candidates"
        self.candidates_dir.mkdir(parents=True, exist_ok=True)
        self.budget = budget
        # Shared across generations and owned by the driver when there is one:
        # the seed is a perfectly good twin target, and so is a candidate from
        # six generations ago.
        self.signatures = signatures if signatures is not None else BehaviourIndex()

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
            for cid in alive:
                outcome = self._run_stage(stage, by_id[cid], paths[cid])
                self._absorb(results[cid], outcome, stage)
                if not outcome.ok:
                    continue
                # A candidate that behaved exactly like one already evaluated
                # at this stage keeps its score, but buys nothing further: no
                # deeper stage, no hold-out run, no archive entry.
                if self._note_behaviour(results[cid], outcome, stage, cid):
                    continue
                survivors.append(cid)

            is_final = index == len(stages) - 1
            if is_final:
                self._run_private(stage, by_id, paths, results, survivors)
                break

            scored = [(cid, results[cid].score) for cid in survivors]
            alive = select_promoted(scored, stage.promote, prior)

        return results

    # -- internals -------------------------------------------------------

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
        )
        if self._is_final(stage) and self.budget is not None:
            self.budget.record_full_eval()
        return outcome

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
        if not outcome.ok:
            result.last_failure = _artefact(outcome)
            if stage.kind == "builtin-static":
                result.rejected = True
                result.reject_reason = outcome.failure
            result.score = self.config.evaluate.failure_score
            result.ranking_score = result.score
            return

        result.stages_reached.append(stage.id)
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
        if not stage.private_inputs or stage.kind != "command":
            return
        for cid in survivors:
            outcome = run_command_stage(
                paths[cid],
                stage,
                inputs=stage.private_inputs,
                out_path=self.work_dir / "stage_out" / f"{cid}.{stage.id}.private.json",
                cwd=self.config.base_dir,
                private=True,
            )
            result = results[cid]
            result.outcomes.append(outcome)
            if not outcome.ok:
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
