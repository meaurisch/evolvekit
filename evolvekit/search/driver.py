"""The Phase B search driver: an archive, sampled parents, and a novelty gate.

One generation is:

    sample P parents from the archive's elites   (sigmoid(z) x 1/(1+children))
    pick 1-2 inspirations from *other* cells
    mutate                diff / rewrite / crossover / param_lhs / big_step
    novelty filter        AST-normalised hash, before anything is evaluated
    evaluate              the staged cascade
    insert                one elite per cell, ranked by the hold-out-aware score

Phase A's parent rule was "best-so-far, or one of the top k", which applied no
diversity pressure at all. The novelty gate and the ranking score both come
straight out of the first real run: 40 % of its paid calls were duplicates or
no-ops, and its winner improved the public set while losing on the hold-out.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from evolvekit.budget import BudgetGuard, StopPolicy
from evolvekit.candidate import Candidate, extract_block, splice_block
from evolvekit.config import Config
from evolvekit.deltas import delta_summary
from evolvekit.economics import GenerationPoint, series
from evolvekit.evaluate.cascade import Cascade
from evolvekit.evaluate.signature import BehaviourIndex
from evolvekit.evaluate.types import EvalResult
from evolvekit.ledger import Ledger
from evolvekit.lock import run_lock
from evolvekit.prompts import Inspiration
from evolvekit.providers import Provider, build_provider
from evolvekit.search.archive import Archive
from evolvekit.search.breadth import AdaptiveBreadth
from evolvekit.search.novelty import NoveltyIndex, NoveltyVerdict, build_near_backend
from evolvekit.search.operators import OPERATOR_ROLES, OperatorResult, param_lhs, run_operator
from evolvekit.search.params import has_params
from evolvekit.search.scratchpad import Scratchpad

__all__ = ["Driver", "RunSummary", "SEED_OPERATOR"]

SEED_OPERATOR = "human-seed"


@dataclass
class RunSummary:
    generations: int = 0
    candidates: int = 0
    rejected: int = 0
    no_ops: int = 0
    duplicates: int = 0
    behavioural: int = 0
    near: int = 0
    embedding_calls: int = 0
    children_per_generation: int = 0
    """The breadth the run ended on. Equal to the configured value unless
    `search.adaptive_children` moved it."""
    cells: int = 0
    occupancy: str = ""
    best: Candidate | None = None
    seed_score: float | None = None
    stop_reason: str = "generations exhausted"
    totals: dict[str, float] = field(default_factory=dict)
    economics: list[GenerationPoint] = field(default_factory=list)
    """One point per generation: cumulative spend, best, and what the last
    window of generations cost per unit of improvement."""

    @property
    def improvement(self) -> float | None:
        if self.best is None or self.best.score is None or self.seed_score is None:
            return None
        return self.best.score - self.seed_score

    @property
    def wasted_calls(self) -> int:
        """Children the novelty filter refused to pay to evaluate."""
        return self.no_ops + self.duplicates

    @property
    def rejection_breakdown(self) -> str:
        """`N (x no-op, y duplicate, z behavioural)` -- the run's wasted spend."""
        return (
            f"{self.rejected} ({self.no_ops} no-op, {self.duplicates} duplicate, "
            f"{self.behavioural} behavioural)"
        )

    @property
    def near_breakdown(self) -> str:
        """Near-duplicates were evaluated, so they are counted apart from the
        rejections: they cost an evaluation, not a wasted one."""
        suffix = (
            f", {self.embedding_calls} embedding call(s)"
            if self.embedding_calls
            else ""
        )
        return f"{self.near} flagged, evaluated anyway{suffix}"


class Driver:
    def __init__(
        self,
        config: Config,
        *,
        run_dir: str | Path,
        providers: dict[str, Provider] | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.config = config
        self.ledger = Ledger(run_dir)
        self.budget = BudgetGuard(config.budget)
        self.stop_policy = StopPolicy(config.stop)
        self.behaviour = BehaviourIndex()
        self.cascade = Cascade(
            config,
            work_dir=self.ledger.run_dir / "work",
            budget=self.budget,
            signatures=self.behaviour,
        )
        self.rng = random.Random(config.search.seed)
        self.breadth = AdaptiveBreadth.from_config(config.search)
        self.log = log or (lambda _msg: None)
        self._providers = providers or {}

        skeleton = config.problem.skeleton.read_text(encoding="utf-8")
        self.prefix, self.seed_block, self.suffix = extract_block(
            skeleton, config.problem.block_start, config.problem.block_end
        )
        self.skeleton_source = skeleton
        self.archive: list[Candidate] = []
        self.grid = Archive.from_config(config.search.archive)
        near_cfg = config.search.novelty.near
        self._embed_context: tuple[str, int] = ("startup", 0)
        self.novelty = NoveltyIndex(
            near=build_near_backend(
                near_cfg,
                provider=self.provider("embed") if near_cfg.method == "embedding" else None,
                on_call=self._bill_embedding,
            ),
            threshold=near_cfg.threshold,
        )
        self.scratchpad = Scratchpad(self.ledger.run_dir, config.search.scratchpad_every)
        self.results: dict[str, EvalResult] = {}
        self.by_id: dict[str, Candidate] = {}
        self._counter = 0
        self._resumed_generation = 0
        self._provider_failures = 0
        self._provider_halt: str | None = None
        self._dead_ends: list[str] = []
        self._parent_notes: dict[str, str] = {}
        """One artefact per parent that bred a behavioural twin, spent on that
        parent's next prompt. The parent's children counter has already been
        incremented, so the archive has made it less attractive; this tells the
        model *why* the last attempt on it bought nothing."""

    # -- providers -------------------------------------------------------

    def provider(self, role: str) -> Provider:
        if role not in self._providers:
            self._providers[role] = build_provider(
                self.config.models.by_role(role), base_dir=self.config.base_dir
            )
        return self._providers[role]

    def _bill_embedding(self, tokens: int, texts: int) -> None:
        """Charge one embedding call to whichever child provoked it."""
        candidate_id, generation = self._embed_context
        usage = self.ledger.record_embedding(
            self.config.models.by_role("embed"),
            tokens=tokens,
            texts=texts,
            candidate_id=candidate_id,
            generation=generation,
        )
        self.budget.spend(usd=usage.usd, tokens=usage.input_tokens)

    # -- helpers ---------------------------------------------------------

    def _next_id(self, generation: int) -> str:
        self._counter += 1
        return f"g{generation:03d}-c{self._counter:04d}"

    def _make_source(self, block: str) -> str:
        return splice_block(
            self.skeleton_source,
            block,
            self.config.problem.block_start,
            self.config.problem.block_end,
        )

    @property
    def best(self) -> Candidate | None:
        ranked = self.top_k(1)
        return ranked[0] if ranked else None

    def top_k(self, k: int) -> list[Candidate]:
        """Best first, by the hold-out-aware ranking score."""
        alive = [c for c in self.archive if not c.rejected and c.fitness is not None]
        return sorted(alive, key=lambda c: (-(c.fitness or 0.0), c.id))[:k]

    def _economics(self) -> list[GenerationPoint]:
        """The cost-per-progress series as of right now.

        Rebuilt from the two logs rather than accumulated, for the same reason
        the archive is: the logs are the record, and a series that drifts from
        them would be worse than no series.
        """
        return series(
            self.ledger.runs(),
            self.ledger.usage(),
            window=self.config.stop.economics_window,
            epsilon=self.config.stop.epsilon,
        )

    def _archive_scores(self) -> list[float]:
        return [
            c.fitness
            for c in self.archive
            if not c.rejected and c.fitness is not None
        ]

    # -- resume ----------------------------------------------------------

    def resume(self) -> int:
        """Rebuild the archive from `runs.jsonl`. Returns the last generation.

        `archive.json` is only ever a snapshot; the JSONL is the record. A run
        that died between the last append and the last snapshot still comes
        back consistent, which is the whole reason the rebuild reads the log.
        """
        rows = self.ledger.runs()
        if not rows:
            return 0
        self.grid = Archive.from_records(rows, self.config.search.archive)
        last_generation = 0
        for row in rows:
            candidate = Candidate.from_record(row)
            candidate.source = self._make_source(candidate.block)
            self.archive.append(candidate)
            self.by_id[candidate.id] = candidate
            if not candidate.rejected:
                self._embed_context = (candidate.id, int(candidate.generation))
                self.novelty.add(candidate.id, candidate.block)
            # Signatures come back even for a rejected twin: it is the *first*
            # id per signature that matters, and dropping it would let the same
            # behaviour be paid for again after a resume.
            for stage_id, signature in (candidate.behaviour_signatures or {}).items():
                self.behaviour.add(candidate.id, stage_id, signature)
            if candidate.novelty == "behavioural":
                self._remember_dead_end(candidate)
            last_generation = max(last_generation, int(candidate.generation))
            suffix = candidate.id.rsplit("-c", 1)[-1]
            if suffix.isdigit():
                self._counter = max(self._counter, int(suffix))
        self._resumed_generation = last_generation
        return last_generation

    # -- the loop --------------------------------------------------------

    def run(self, generations: int | None = None) -> RunSummary:
        """Take the run lock, then search. One writer per run directory."""
        with run_lock(self.ledger.run_dir) as lock:
            if lock.reclaimed_from:
                self.log(
                    f"reclaimed a stale lock from dead pid {lock.reclaimed_from}"
                )
            return self._run(generations)

    def _run(self, generations: int | None) -> RunSummary:
        total = generations if generations is not None else self.config.search.generations
        summary = RunSummary()

        started_at = self.resume()
        if started_at:
            self.log(
                f"resumed {len(self.archive)} candidate(s) from runs.jsonl "
                f"({self.grid.occupancy()})"
            )
            seed = next((c for c in self.archive if c.operator == SEED_OPERATOR), None)
            summary.seed_score = seed.score if seed else None
            summary.candidates = len(self.archive)
        else:
            seed = self._seed_candidate()
            self._evaluate_and_record([seed], generation=0)
            summary.seed_score = seed.score
            summary.candidates += 1
            self.log(f"gen 0  seed        score={_fmt(seed.score)}")

            # A seed that cannot get through the cascade means the harness is
            # broken, not the heuristic. Stop here, before the first LLM call,
            # so a misconfigured evaluator never costs money.
            if (
                seed.rejected
                or seed.last_failure
                or seed.score == self.config.evaluate.failure_score
            ):
                detail = (seed.reject_reason or seed.last_failure or "scored failure_score").strip()
                first_line = detail.splitlines()[0] if detail else "unknown failure"
                summary.stop_reason = (
                    "seed failed evaluation — fix the harness before spending: "
                    f"{first_line}"
                )
                self.log(f"ABORT: {summary.stop_reason}")
                return self._finalise(summary)

        points = self._economics()
        decision = self.stop_policy.update(
            self.best.fitness if self.best else None, points[-1] if points else None
        )

        for offset in range(1, total + 1):
            generation = started_at + offset
            verdict = self.budget.check()
            if not verdict.allowed:
                summary.stop_reason = verdict.reason or "budget exhausted"
                break
            if decision.stop:
                summary.stop_reason = decision.reason or "stop policy"
                break

            self._maybe_refresh_scratchpad(generation)

            children = self._breed(generation, plateau=decision.plateau)
            summary.candidates += len(children)
            viable = [c for c in children if not c.rejected]
            self._evaluate_and_record(viable, generation=generation)
            for child in children:
                if child.rejected and child.id not in self.results:
                    self._record(child)
            summary.generations = offset

            if self._provider_is_dead():
                summary.stop_reason = (
                    f"provider failing ({self._provider_failures} consecutive errors): "
                    f"{self._provider_halt}"
                )
                self.log(f"ABORT: {summary.stop_reason}")
                break

            best = self.best
            points = self._economics()
            point = points[-1] if points else None
            decision = self.stop_policy.update(best.fitness if best else None, point)
            self.log(
                f"gen {generation:<2} {len(children)} child(ren)  "
                f"best={_fmt(best.fitness if best else None)}  "
                f"cells={len(self.grid.cells)}  "
                f"spent=${self.budget.state.usd:.4f}"
            )
            if self.breadth is not None:
                change = self.breadth.update(decision.stagnant_generations == 0)
                self.log(f"        {change}")
            if point is not None:
                self.log(
                    f"        economics  cum=${point.cumulative_usd:.4f}  "
                    f"since-improvement=${point.usd_since_improvement:.4f}  "
                    f"window({point.window})={point.gain_line}"
                )
            if decision.stop:
                summary.stop_reason = decision.reason or "stop policy"
                break

        return self._finalise(summary)

    def _finalise(self, summary: RunSummary) -> RunSummary:
        summary.best = self.best
        summary.rejected = sum(1 for c in self.archive if c.rejected)
        summary.no_ops = self.grid.no_ops
        summary.duplicates = self.grid.duplicates
        summary.behavioural = sum(
            1 for c in self.archive if c.novelty == "behavioural"
        )
        summary.near = sum(1 for c in self.archive if c.novelty == "near")
        summary.embedding_calls = self.ledger.embedding_calls
        summary.cells = len(self.grid.cells)
        summary.occupancy = self.grid.occupancy()
        summary.totals = self.ledger.totals()
        summary.economics = self._economics()
        summary.children_per_generation = self.children_per_generation
        archive_payload = self.grid.to_dict()
        archive_payload["economics_window"] = self.config.stop.economics_window
        self.ledger.write_archive(archive_payload)
        return summary

    # -- generation internals -------------------------------------------

    def _seed_candidate(self) -> Candidate:
        return Candidate(
            id=self._next_id(0),
            generation=0,
            block=self.seed_block,
            source=self.skeleton_source,
            operator=SEED_OPERATOR,
            model="human",
            provider="human",
            role="human",
        )

    @property
    def children_per_generation(self) -> int:
        """Today's breadth: the adaptive value when there is one, else config."""
        if self.breadth is None:
            return self.config.search.children_per_generation
        return self.breadth.current

    def _plan_operators(self, generation: int, plateau: bool) -> list[str]:
        """One operator name per child. The big step, when due, takes slot 0."""
        count = self.children_per_generation
        weights = self.config.search.operators
        sweepable = has_params(self.seed_block)
        names = [
            n
            for n, w in sorted(weights.items())
            if w > 0 and (n != "param_lhs" or sweepable)
        ]
        shares = [weights[n] for n in names]
        if not names:  # only param_lhs configured, against a param-less skeleton
            names, shares = ["rewrite"], [1.0]

        plan = [self.rng.choices(names, weights=shares, k=1)[0] for _ in range(count)]
        scheduled = generation % self.config.search.big_step_every == 0
        if scheduled or plateau:
            plan[0] = "big_step"
            self.stop_policy.note_big_step()
        return plan

    def _parent_pool(self, wanted: int) -> list[Candidate]:
        parents = self.grid.sample_parents(wanted, self.rng)
        if parents:
            return parents
        fallback = self.best or self._seed_candidate()
        return [fallback for _ in range(max(1, wanted))]

    def _score_line(self, candidate: Candidate) -> str:
        parts = [f"score {_fmt(candidate.fitness)}"]
        if candidate.public_score is not None:
            parts.append(f"public {_fmt(candidate.public_score)}")
        if candidate.private_score is not None:
            parts.append(f"private {_fmt(candidate.private_score)}")
        if candidate.generalization_gap is not None:
            parts.append(f"gap {candidate.generalization_gap:+.4g}")
        return ", ".join(parts)

    def _summary_of(self, candidate: Candidate) -> str:
        parent = self.by_id.get(candidate.parent_id or "")
        return delta_summary(
            candidate.block,
            parent.block if parent else None,
            candidate.parent_id,
        )

    def _inspirations(self, parent: Candidate, operator: str) -> list[Inspiration]:
        """1-2 elites from other cells; only crossover is shown their code."""
        budget = min(self.config.search.inspirations, 2)
        if budget <= 0:
            return []
        wanted = 1 if operator == "crossover" else self.rng.randint(1, budget)
        picks = self.grid.sample_inspirations(parent, wanted, self.rng)
        return [
            Inspiration(
                id=c.id,
                summary=self._summary_of(c),
                score_line=self._score_line(c),
                block=c.block if operator == "crossover" else None,
            )
            for c in picks
        ]

    # -- dead ends: behavioural twins, shown to every parent -------------

    DEAD_END_LIMIT = 6

    def _remember_dead_end(self, candidate: Candidate) -> None:
        """Keep a one-line fingerprint of a behavioural twin for every later prompt.

        The per-parent note below only reaches the parent that bred the twin.
        The second real Phase C run bred nine twins of best-fit from four
        different parents (`** 0.9`, `sqrt`, `+ 0.01 * b`, tie-break epsilons):
        the lesson has to reach every prompt, not one lineage.
        """
        line = _fingerprint_line(candidate.block)
        if not line:
            return
        entry = f"`{line}` (child of {candidate.parent_id or '?'}) == {candidate.behaviour_twin_id}"
        if entry not in self._dead_ends:
            self._dead_ends.append(entry)

    def _compose_hint(self, parent: Candidate) -> str | None:
        parts: list[str] = []
        note = self._parent_notes.pop(parent.id, None)
        if note:
            parts.append(note)
        if self._dead_ends:
            recent = self._dead_ends[-self.DEAD_END_LIMIT :]
            parts.append(
                "Already tried -- each of these produced exactly the behaviour of an "
                "earlier program on every evaluated instance, so it bought nothing:\n"
                + "\n".join(f"- {e}" for e in recent)
                + "\nRe-scaling, re-parametrising or tie-breaking an existing rule "
                "changes none of its decisions. Change which option wins."
            )
        return "\n\n".join(parts) if parts else None

    def _provider_is_dead(self) -> bool:
        """True once the backend has failed `max_consecutive_provider_errors` times in a row.

        The first real Phase C runs hit a subscription session cap after one
        call and then paid nothing for 22 more children -- each a rejected
        record, each a wasted slot, and a 'rejected: 23' summary that looked
        like a search problem instead of a dead backend.
        """
        return self._provider_failures >= self.config.budget.max_consecutive_provider_errors

    def _breed(self, generation: int, *, plateau: bool) -> list[Candidate]:
        plan = self._plan_operators(generation, plateau)
        pool = self._parent_pool(self.config.search.parents_wanted(len(plan)))
        best = self.best
        children: list[Candidate] = []
        for slot, operator in enumerate(plan):
            if not self.budget.check().allowed or self._provider_is_dead():
                break
            # A big step is an escape from the *front* of the search, so it
            # starts from the best candidate rather than from a sampled cell.
            parent = (best or pool[0]) if operator == "big_step" else pool[slot % len(pool)]
            children.append(self._breed_one(generation, operator, parent))
        return children

    def _breed_one(
        self, generation: int, operator: str, parent: Candidate
    ) -> Candidate:
        candidate_id = self._next_id(generation)
        self.grid.note_child(parent.id)

        inspirations = self._inspirations(parent, operator)
        if operator == "crossover" and not inspirations:
            # Nothing to cross with yet: one cell, or one elite. Mutate instead
            # of pretending, and say so in the record.
            operator = "rewrite"
        role = "strong" if operator == "big_step" else OPERATOR_ROLES.get(operator, "small")

        outcome, verdict, attempts = self._propose(
            operator, parent, inspirations, candidate_id, generation, role
        )

        child = Candidate(
            id=candidate_id,
            generation=generation,
            block=outcome.block if outcome.ok else parent.block,
            source=self._make_source(outcome.block if outcome.ok else parent.block),
            operator=outcome.mode if outcome.ok and outcome.mode else operator,
            model=(
                outcome.completion.model
                if outcome.completion
                else ("none" if role == "none" else self.config.models.by_role(role).model)
            ),
            provider=(
                "none" if role == "none" else self.config.models.by_role(role).provider
            ),
            parent_id=parent.id,
            role=role,
            inspiration_ids=[i.id for i in inspirations],
        )
        if outcome.completion is not None:
            self._provider_failures = 0
        elif outcome.provider_error:
            self._provider_failures += 1
            self._provider_halt = outcome.error or "provider error"
        if not outcome.ok:
            child.rejected = True
            child.reject_reason = outcome.error
            child.last_failure = outcome.error
            child.score = self.config.evaluate.failure_score
        elif verdict is not None and verdict.rejects:
            child.rejected = True
            child.novelty = verdict.kind
            child.twin_id = verdict.twin_id
            child.reject_reason = verdict.reason
            child.score = self.config.evaluate.failure_score
            if verdict.kind == "no_op":
                self.grid.no_ops += 1
            else:
                self.grid.duplicates += 1
            self.log(
                f"        {candidate_id} {verdict.kind} after {attempts} attempt(s): "
                f"{verdict.reason}"
            )
        elif verdict is not None and verdict.near:
            # Flagged, not refused. The re-prompt already had its one chance;
            # refusing here as well is how a similarity filter starves a search
            # that has only twelve samples to begin with.
            child.novelty = "near"
            child.near_twin_id = verdict.twin_id
            child.similarity = verdict.similarity
            self.grid.near_duplicates += 1
            self.log(
                f"        {candidate_id} near-duplicate after {attempts} "
                f"attempt(s): {verdict.reason} -- evaluating anyway"
            )
        if operator == "big_step":
            child.operator = f"big_step/{child.operator}"
        elif operator == "crossover" and outcome.ok:
            child.operator = "crossover"
        return child

    def _propose(
        self,
        operator: str,
        parent: Candidate,
        inspirations: list[Inspiration],
        candidate_id: str,
        generation: int,
        role: str,
    ) -> tuple[OperatorResult, NoveltyVerdict | None, int]:
        """Run the operator, then the novelty gate, with at most one re-prompt."""
        attempts = 0
        self._embed_context = (candidate_id, generation)
        hint = self._compose_hint(parent)
        outcome = self._invoke(operator, parent, inspirations, role, hint)
        attempts += 1
        self._bill(outcome, candidate_id, generation, operator, role)
        self._trace(outcome, candidate_id, parent, operator, role, attempt=attempts)
        if not outcome.ok:
            return outcome, None, attempts

        wants_near = role != "none"
        verdict = self.novelty.check(
            outcome.block or "", parent.block, parent.id, near=wants_near
        )
        if verdict.novel or not self.config.search.novelty_retry or role == "none":
            return outcome, verdict, attempts
        if not self.budget.check().allowed:
            return outcome, verdict, attempts

        # One re-prompt, ever. The first real run showed the small model
        # handing back the same program three times; asking twice is worth the
        # cheap call, asking a third time is how v1 built its token bill.
        retry = self._invoke(operator, parent, inspirations, role, verdict.retry_hint)
        attempts += 1
        self._bill(retry, candidate_id, generation, operator, role)
        self._trace(retry, candidate_id, parent, operator, role, attempt=attempts)
        if not retry.ok:
            return outcome, verdict, attempts
        return (
            retry,
            self.novelty.check(
                retry.block or "", parent.block, parent.id, near=wants_near
            ),
            attempts,
        )

    def _invoke(
        self,
        operator: str,
        parent: Candidate,
        inspirations: list[Inspiration],
        role: str,
        hint: str | None,
    ) -> OperatorResult:
        if operator == "param_lhs":
            return param_lhs(parent, seed=self.rng.randrange(1 << 30))
        return run_operator(
            operator,
            config=self.config,
            provider=self.provider(role),
            model_role=role,
            parent=parent,
            parent_result=self.results.get(parent.id),
            skeleton_prefix=self.prefix,
            skeleton_suffix=self.suffix,
            best_score=self.best.fitness if self.best else None,
            inspirations=inspirations,
            scratchpad=self.scratchpad.text or None,
            extra_instruction=hint,
        )

    def _bill(
        self,
        outcome: OperatorResult,
        candidate_id: str,
        generation: int,
        operator: str,
        role: str,
    ) -> None:
        if outcome.completion is None or role == "none":
            return
        usage = self.ledger.record_usage(
            outcome.completion,
            self.config.models.by_role(role),
            candidate_id=candidate_id,
            generation=generation,
            operator=operator,
        )
        self.budget.spend(usd=usage.usd, tokens=usage.input_tokens + usage.output_tokens)

    def _trace(
        self,
        outcome: OperatorResult,
        candidate_id: str,
        parent: Candidate,
        operator: str,
        role: str,
        *,
        attempt: int,
    ) -> None:
        name = candidate_id if attempt == 1 else f"{candidate_id}.retry{attempt - 1}"
        self.ledger.write_trace(
            name,
            messages=outcome.messages,
            response=outcome.completion.text if outcome.completion else None,
            error=outcome.error,
            meta={
                "operator": operator,
                "parent_id": parent.id,
                "apply_mode": outcome.mode,
                "role": role,
                "attempt": attempt,
                **({"params": outcome.meta["params"]} if "params" in outcome.meta else {}),
            },
        )

    # -- the meta-scratchpad ---------------------------------------------

    def _maybe_refresh_scratchpad(self, generation: int) -> None:
        if not self.scratchpad.due(generation) or not self.budget.check().allowed:
            return
        elites = self.grid.elites()[:8]
        if not elites:
            return
        entries = [
            f"{c.id} (gen {c.generation}, {c.operator}) {self._score_line(c)} "
            f"-- {self._summary_of(c)}"
            for c in elites
        ]
        text, completion, messages, error = self.scratchpad.refresh(
            config=self.config,
            provider=self.provider("small"),
            entries=entries,
            generation=generation,
        )
        trace_id = f"scratchpad-g{generation:03d}"
        if completion is not None:
            usage = self.ledger.record_usage(
                completion,
                self.config.models.by_role("small"),
                candidate_id=trace_id,
                generation=generation,
                operator="scratchpad",
            )
            self.budget.spend(
                usd=usage.usd, tokens=usage.input_tokens + usage.output_tokens
            )
        self.ledger.write_trace(
            trace_id,
            messages=messages,
            response=completion.text if completion else None,
            error=error,
            meta={"operator": "scratchpad", "generation": generation},
            subdir="meta",
        )
        if error is None:
            self.log(f"        scratchpad refreshed: {len(text.splitlines())} line(s)")

    # -- evaluation and logging -----------------------------------------

    def _evaluate_and_record(
        self, candidates: Sequence[Candidate], *, generation: int
    ) -> None:
        if not candidates:
            return
        results = self.cascade.evaluate_generation(candidates, self._archive_scores())
        for candidate in candidates:
            result = results[candidate.id]
            self.results[candidate.id] = result
            candidate.score = result.score
            candidate.rejected = result.rejected
            candidate.reject_reason = result.reject_reason
            candidate.kpis = result.kpis
            candidate.kpi_cv = result.kpi_cv
            candidate.penalty_total = result.penalty_total
            candidate.stage_scores = result.stage_scores
            candidate.stages_reached = result.stages_reached
            candidate.public_score = result.public_score
            candidate.private_score = result.private_score
            candidate.generalization_gap = result.generalization_gap
            candidate.ranking_score = result.ranking_score
            candidate.last_failure = result.last_failure
            candidate.feedback = result.feedback
            candidate.behaviour_signatures = result.behaviour_signatures
            candidate.behaviour_twin_id = result.behaviour_twin_id
            if result.behaviour_twin_id is not None:
                candidate.novelty = "behavioural"
                self._note_behavioural_twin(candidate)
            # Placement first: the cell coordinate belongs in the ledger row.
            self.grid.add(candidate)
            if not candidate.rejected:
                self.novelty.add(candidate.id, candidate.block)
            self._record(candidate)

    def _note_behavioural_twin(self, candidate: Candidate) -> None:
        """Log the twin and leave an artefact for the parent's next prompt.

        The wording is deliberately problem-agnostic: the loop knows only that
        every number the evaluator reported came back identical, which means
        the child made the same decisions as an earlier program however
        differently it was written. What "decision" means is the problem
        description's business, not the framework's.
        """
        self.log(f"        {candidate.id}: {candidate.reject_reason}")
        self._remember_dead_end(candidate)
        if not candidate.parent_id:
            return
        self._parent_notes[candidate.parent_id] = (
            f"Previous child {candidate.id} produced identical behaviour to "
            f"{candidate.behaviour_twin_id} on every evaluated instance -- it was "
            "a re-expression of the same rule, not a new one. Change the "
            "decisions the program makes, not the way it states them."
        )

    def _record(self, candidate: Candidate) -> None:
        usage = [
            row
            for row in self.ledger.usage()
            if row.get("candidate_id") == candidate.id
        ]
        candidate.tokens_in = sum(int(r.get("input_tokens", 0)) for r in usage)
        candidate.tokens_out = sum(int(r.get("output_tokens", 0)) for r in usage)
        candidate.usd = sum(float(r.get("usd", 0.0)) for r in usage)
        self.archive.append(candidate)
        self.by_id[candidate.id] = candidate
        self.ledger.record_run(candidate.to_record())


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.6g}"


def _fingerprint_line(block: str, limit: int = 90) -> str:
    """The last line of code in a block, trimmed -- enough to recognise a rule."""
    code = [
        line.strip()
        for line in block.splitlines()
        if line.strip() and not line.strip().startswith(("#", '"""', "'''"))
    ]
    if not code:
        return ""
    line = code[-1]
    return line if len(line) <= limit else line[: limit - 3] + "..."
