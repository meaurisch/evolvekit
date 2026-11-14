"""The Phase B search driver: an archive, sampled parents, and a novelty gate.

One generation is:

    sample P parents from the archive's elites   (sigmoid(z) x 1/(1+children))
    pick 1..N inspirations from *other* cells    (N = search.inspirations)
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

import hashlib
import json
import math
import os
import random
import shlex
import socket
import sys
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

from evolvekit import __version__
from evolvekit.budget import AI_USED_UP, BudgetGuard, StopDecision, StopPolicy
from evolvekit.candidate import (
    SEED_OPERATOR,
    BlockError,
    Candidate,
    extract_block,
    splice_block,
)
from evolvekit.config import TYPED_SPACE_OPERATORS, Config
from evolvekit.deltas import change_summary
from evolvekit.economics import GenerationPoint, series
from evolvekit.evaluate.cascade import Cascade, finished_final_stage, holdout_veto, race_warnings
from evolvekit.evaluate.scoring import ranking_score
from evolvekit.evaluate.signature import Behaviour, BehaviourIndex
from evolvekit.evaluate.stages import restore_build
from evolvekit.evaluate.types import EvalResult
from evolvekit.events import EventLog, Heartbeat, read_events
from evolvekit.leaderboard import (
    REEVALUATION_SEED_BASE,
    pool_rows,
    read_reevaluations,
    seed_private,
    seed_public,
)
from evolvekit.ledger import Ledger, _atomic_write
from evolvekit.lock import run_lock
from evolvekit.prompts import Inspiration, LedgerEntry, big_step_reason
from evolvekit.providers import Provider, build_provider
from evolvekit.providers.base import CallCapped, supports_call_cap
from evolvekit.search.archive import Archive
from evolvekit.search.breadth import AdaptiveBreadth
from evolvekit.search.novelty import NoveltyIndex, NoveltyVerdict, build_near_backend
from evolvekit.search.operators import (
    OPERATOR_ROLES,
    OperatorResult,
    TreeContext,
    latin_rows,
    param_cross,
    param_lhs,
    param_lhs_typed,
    param_local,
    param_tpe,
    run_operator,
)
from evolvekit.search.lhs import latin_hypercube, rows_for_params
from evolvekit.search.params import current_values, declared_ranges, has_params
from evolvekit.search.scratchpad import Scratchpad
from evolvekit.search.screening import SCREENING_FILE, ScreenControl
from evolvekit.search.tried import _counter as _id_counter
from evolvekit.search.tried import entry_for, parent_ledger
from evolvekit.search.tuning import Observation
from evolvekit.space import STEP_SCALE
from evolvekit.stopping import RunStop, active_seconds, ai_seconds

__all__ = ["Driver", "RunSummary", "SEED_OPERATOR"]


def _random_stream(seed: int, recorded: int) -> random.Random:
    """The run's generator: reproducible, and never the same stream twice.

    A fresh run is seeded with `search.seed`, as it always was. A resumed run
    seeded the same way would draw the same operators, parents and seeds as its
    first generation did -- and an operator that samples from such a seed alone
    breeds blocks the run already holds, which the novelty filter discards.
    Folding in how much the directory already holds gives a resumed run a
    stream of its own that is still a function of the seed and the directory,
    so resuming stays reproducible too. (`param_lhs` rows do not come from
    this stream on resume: the run's hypercube and its next row are kept in
    `work/lhs.json`, written as each row is handed out, so a resumed run goes
    on from the next row. One window is left: a run that dies while it breeds
    a round, before that round's `pending.json` exists, has handed out rows
    that no child kept -- the resumed run breeds the round anew from the rows
    after them, so those rows are skipped.)
    """
    return random.Random(seed if recorded == 0 else f"{seed}/{recorded}")


@dataclass
class RunSummary:
    aborted: bool = False
    """The run stopped because it could not work -- the seed failed its own
    evaluation, or the model backend kept failing -- rather than because it
    was done. `stop_reason` says which; `run` exits 4."""
    generations: int = 0
    candidates: int = 0
    rejected: int = 0
    no_ops: int = 0
    duplicates: int = 0
    behavioural: int = 0
    near: int = 0
    unfinished: int = 0
    """Candidates that were evaluated but did not finish the final stage: a
    failed or timed-out evaluation, a proxy-only candidate, a skipped final
    stage, a failed hold-out. Recorded, never ranked -- and a number that is
    climbing is an evaluator problem, not a search problem. A candidate kept
    out by the hold-out veto finished, and is counted in `vetoed` instead."""
    vetoed: int = 0
    """Candidates that finished the final stage with a hold-out score below
    the seed's (`evaluate.holdout_veto`): recorded, never ranked."""
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
        allow_changed_problem: bool = False,
    ) -> None:
        self.config = config
        self.allow_changed_problem = allow_changed_problem
        self.ledger = Ledger(run_dir)
        self._reevaluations: list[dict] = read_reevaluations(self.ledger.run_dir)
        """`reevaluations.jsonl` (`search.reevaluate_best_every`): every reading
        of `runs.jsonl` here is pooled with it (`_replayed_rows`)."""
        self.budget = BudgetGuard(config.budget)
        # `min_big_steps` holds a plateau open until the strong model has been
        # tried. A run without a model has no big step to wait for, and would
        # never be allowed to stop on patience. The same relief applies to the
        # economics rules, which otherwise wait for a big step that never comes.
        self.stop_policy = StopPolicy(
            config.stop
            if config.search.takes_big_steps
            else replace(config.stop, min_big_steps=0),
            takes_big_steps=config.search.takes_big_steps,
        )
        self.behaviour = BehaviourIndex()
        self.events = EventLog(self.ledger.run_dir)
        self.heartbeat = Heartbeat(self.ledger.run_dir, session=self.events.session)
        self.run_stop = RunStop(self.ledger.run_dir, max_hours=config.budget.max_hours)
        """`budget.max_hours` and `stop-request` (`evolvekit/stopping.py`)."""
        self._durations: dict[int, float] = {}
        """How long each generation took, by generation, all sessions included:
        what the time limit projects the next one from."""
        self._seed_stages: dict[str, float] = {}
        """How long the seed took in each command stage (`stage_finished`), all
        sessions included: what the time limit projects the first round from."""
        self._generation_now: int | None = None
        self.cascade = Cascade(
            config,
            work_dir=self.ledger.run_dir / "work",
            budget=self.budget,
            signatures=self.behaviour,
            on_event=self._on_evaluation,
            stop=self.run_stop.event,
            time_left=self.run_stop.left_s,
            log=self.log,
        )
        self.rng = _random_stream(config.search.seed, len(self.ledger.runs()))
        self._lhs: dict[str, dict] = self._read_lhs()
        """The run's Latin hypercubes (`work/lhs.json`), one per space: what
        `param_lhs` children take their rows from, in order (`_next_lhs_row`)."""
        self.step_scale: float
        self._step_history: list[dict]
        self.step_scale, self._step_history = self._read_perturb()
        """The sd of a local step, as a share of each parameter's range
        (`ParameterSpace.perturb`; `STEP_SCALE` at first): what `param_local`
        and the local-step fallbacks of `param_cross` and `param_tpe` step with.
        Adapted after each round by the 1/5 rule (`_adapt_step`), kept with one
        line per round in `work/perturb.json`."""
        self._taken: list[dict] = []
        """The configurations proposed so far in this generation (`_breed`):
        what `param_tpe` counts as bad before they are evaluated."""
        self.breadth = AdaptiveBreadth.from_config(config.search)
        self.screens: dict[str, ScreenControl] = self._screen_controls()
        """Per stage with `promote.adapt` before a command stage, its screening
        controller (`search/screening.py`), restored from `work/screening.json`."""
        self._apply_screens()
        self._log_sink = log or (lambda _msg: None)
        self._providers = providers or {}

        key: Callable[[str], str] | None = None
        if config.problem.tree is not None:
            # A tree problem: a candidate is a diff against the base, the seed
            # the empty diff, and two diffs are the same change when they
            # normalise alike. Only here is `evolvekit.tree` (and git) needed.
            from evolvekit import tree

            self.repo: tree.Repo | None = tree.Repo(config.problem.tree.repo)
            self.prefix = self.suffix = self.seed_block = self.skeleton_source = ""
            key = tree.normalise_diff
        else:
            self.repo = None
            skeleton = config.problem.skeleton_source()
            self.prefix, self.seed_block, self.suffix = extract_block(
                skeleton, config.problem.block_start, config.problem.block_end
            )
            self.skeleton_source = skeleton
        self.archive: list[Candidate] = []
        self.grid = Archive.from_config(
            config.search.archive, tree=self.repo is not None, space=config.problem.parameters,
            temperature=config.search.selection_temperature,
        )
        near_cfg = config.search.novelty.near
        self._embed_context: tuple[str, int] = ("startup", 0)
        self.novelty = NoveltyIndex(
            near=build_near_backend(
                near_cfg,
                provider=self.provider("embed") if near_cfg.method == "embedding" else None,
                on_call=self._bill_embedding,
            ),
            threshold=near_cfg.threshold,
            key=key,
        )
        self.scratchpad = Scratchpad(self.ledger.run_dir, config.search.scratchpad_every)
        self.results: dict[str, EvalResult] = {}
        self.by_id: dict[str, Candidate] = {}
        self._counter = 0
        self._resumed_generation = 0
        self._provider_failures = 0
        self._provider_halt: str | None = None
        self._dead_ends: list[str] = []
        self._told: set[str] = set()
        """The run-log notes said once per run (`_agent_patch`, `_calling`)."""
        self._ai_out: str | None = None
        """Why no more AI changes are written (`_ai_room`): the search ends after its round."""
        self._big_step_reason: str | None = None
        """Which trigger planned this round's big step (`_plan_operators`), told to the model."""
        self._plateau_step_at: int | None = None
        """The generation of the last applied big step planned while the plateau
        held, whichever trigger fired: no plateau big step for `big_step_every`
        generations after it (`_plateau_step_allowed`). Rebuilt on resume."""
        self._big_step_on_plateau: bool = False
        """Whether this round's big step was planned on the plateau (`_plan_operators`)."""

    # -- what the run says about itself ----------------------------------

    def log(self, message: str) -> None:
        """A console line, kept. A run's stdout is gone with its terminal, or
        still sitting in a pipe buffer when the process is killed; the event
        log is what a second shell, a dashboard or a post-mortem can read."""
        self.events.emit("log", message=message)
        self._log_sink(message)

    def _on_evaluation(self, type: str, **fields: object) -> None:
        self.events.emit(type, **fields)
        duration = fields.get("duration_s")
        if (type == "stage_finished" and self._generation_now == 0 and not fields.get("private")
                and isinstance(duration, (int, float)) and not isinstance(duration, bool)):
            self._seed_stages[str(fields.get("stage"))] = float(duration)
        if type == "eval_started":
            self.heartbeat.update(
                phase="evaluating",
                candidate_id=fields.get("candidate_id"),
                stage=fields.get("stage"),
            )

    def _problem_identity(self) -> dict:
        """What makes two runs runs of the *same problem*: what is evolved, what
        it is scored on, and how. Not the search settings, the budget or the
        number of workers -- those may change between sessions of one run."""
        config = self.config
        space = config.problem.parameters
        tree_problem = config.problem.tree
        identity = {
            "skeleton_sha": hashlib.sha256(self.skeleton_source.encode("utf-8")).hexdigest()[:16],
            "parameters": [
                {"name": p.name, "type": p.type, "choices": list(p.choices)} for p in space
            ] if space is not None else None,
            "objective": config.evaluate.score.objective,
            "direction": config.evaluate.score.direction,
            # Which candidates compete, and how they rank. `None` when there are
            # none: what a run directory from before either existed recorded.
            "gates": [gate.describe() for gate in config.evaluate.gates] or None,
            "levels": [level.describe() for level in config.evaluate.score.levels] or None,
            "stages": [
                {
                    "id": stage.id, "command": stage.command, "seeds": stage.seeds,
                    "inputs": list(stage.inputs), "private_inputs": list(stage.private_inputs),
                    "instances": list(stage.instances), "private_instances": list(stage.private_instances),
                    "normalize": stage.normalize if stage.fans_out else None,
                }
                for stage in config.evaluate.stages
            ],
        }
        if tree_problem is not None:
            # Only for a tree, so a skeleton run's identity reads as it always did.
            # `build` and `app` change what a command runs, so what a score means; so does
            # the interpreter a build installs into -- only when the build names `{python}`,
            # so a program harness's run resumes from a moved evolvekit.
            identity["tree"] = {"base": tree_problem.base, "open": list(tree_problem.open),
                                "build": tree_problem.build, "app": tree_problem.app}
            if "{python}" in tree_problem.build:
                identity["tree"]["python"] = tree_problem.python
        return identity

    def _check_same_problem(self) -> None:
        """Refuse to continue a run directory with a different problem.

        `runs.jsonl` is append-only and the archive is rebuilt from it, so a
        second problem pointed at the same directory breeds from the first
        one's candidates and ranks scores that mean different things against
        each other -- silently. Every session writes down what the problem
        was; the next one is compared with the *latest* of those, so a change
        let through once with `--allow-changed-problem` is the problem from
        then on rather than a refusal at every later session.
        """
        if self.allow_changed_problem or not self.ledger.runs():
            return
        recorded = None
        for event in read_events(self.ledger.run_dir):
            if event.get("type") == "run_started" and isinstance(event.get("problem"), dict):
                recorded = _comparable(event["problem"])
        if recorded is None:
            return  # a run directory from before this was recorded
        now = _comparable(self._problem_identity())
        changed = [key for key in ("objective", "direction", "skeleton_sha", "parameters", "levels", "gates", "stages",
                                   "tree")
                   if recorded.get(key) != now.get(key)]
        if not changed:
            return
        detail = []
        if "stages" in changed:
            before = {s.get("id"): s for s in recorded.get("stages") or []}
            for stage in now["stages"]:
                old = before.get(stage["id"])
                if old is None:
                    detail.append(f"stage {stage['id']!r} is new")
                else:
                    detail += [f"stage {stage['id']!r}: `{k}` changed" for k in stage if old.get(k) != stage[k]]
            detail += [f"stage {sid!r} is gone" for sid in before if sid not in {s["id"] for s in now["stages"]}]
        detail += [
            {"skeleton_sha": "the skeleton (or the generated one) changed",
             "parameters": "the declared parameters changed (names, types or choices)",
             "objective": f"the objective is now {now['objective']!r}, was {recorded.get('objective')!r}",
             "direction": f"the direction is now {now['direction']!r}",
             "gates": "the gates changed (evaluate.gates), so earlier candidates were judged by other rules",
             "levels": "the levels changed (evaluate.score.levels), so earlier scores rank by other rules",
             "tree": "the source's starting commit, its open files, its build, its program or its Python "
                     "changed"}[key]
            for key in changed if key != "stages"
        ]
        raise ValueError(
            f"{self.ledger.run_dir} holds a run of a different problem: " + "; ".join(detail)
            + ". Its candidates were scored under the old definition, so continuing would rank "
            "scores against each other that do not mean the same thing. Use a new --run-dir; or, "
            "if the change really does not affect what a score means, pass --allow-changed-problem"
        )

    def _describe_run(self, *, first_generation: int, planned: int) -> dict:
        """Everything a reader of the run directory needs in order to interpret
        it without the config file: what is optimised, through which stages,
        under which caps, and how far this session intends to go."""
        config = self.config
        final = config.final_stage.id
        return {
            "version": __version__,
            "host": socket.gethostname(),
            "problem": self._problem_identity(),
            "cpus": os.cpu_count(),
            "config_path": str(config.source) if config.source else None,
            "objective": config.evaluate.score.objective,
            "direction": config.evaluate.score.direction,
            "parameters": (
                config.problem.parameters.describe() if config.problem.parameters else None
            ),
            "parameter_constraints": (
                [c.text for c in config.problem.parameters.constraints]
                if config.problem.parameters else None
            ),
            "gates": [gate.describe() for gate in config.evaluate.gates],
            "levels": [level.describe() for level in config.evaluate.score.levels],
            "failure_score": config.evaluate.failure_score,
            "stages": [
                {
                    "id": stage.id,
                    "kind": stage.kind,
                    "timeout_s": stage.timeout,
                    "seeds": stage.seeds,
                    "promote": stage.promote.describe(),
                    "holdout": bool(stage.private_inputs or stage.private_instances),
                    "final": stage.id == final,
                    "instances": list(stage.instance_names()),
                    "normalize": stage.normalize if stage.fans_out else None,
                    "race": (
                        {"after": stage.race.after, "margin_pct": stage.race.margin_pct}
                        if stage.race is not None else None
                    ),
                    "workers": stage.workers,
                    "pin_cpus": list(stage.pin_cpus),
                    "retries": stage.retries,
                }
                for stage in config.evaluate.stages
            ],
            "first_generation": first_generation,
            "generations_planned": planned,
            "children_per_generation": self.children_per_generation,
            "resumed": bool(self.archive),
            "recorded_candidates": len(self.archive),
            "budget": {
                "max_usd": config.budget.max_usd,
                "max_tokens": config.budget.max_tokens,
                "max_full_evals_per_day": config.budget.max_full_evals_per_day,
                "max_hours": config.budget.max_hours,
            },
            "stop": {
                "patience": config.stop.patience,
                "epsilon": config.stop.epsilon,
                "target": config.stop.target,
                "max_usd_since_improvement": config.stop.max_usd_since_improvement,
            },
        }

    # -- providers -------------------------------------------------------

    def provider(self, role: str) -> Provider:
        if role not in self._providers:
            self._providers[role] = build_provider(
                self.config.models.by_role(role), base_dir=self.config.base_dir
            )
        return self._providers[role]

    def _calling(self, role: str) -> Provider:
        """`provider(role)` for a call: a provider that can be told the most one
        reply may spend (claude-cli) is told what is left of `budget.max_usd`
        with every reply (`CallCapped`); one whose CLI cannot take it is named
        once in the run's log."""
        provider = self.provider(role)
        if not supports_call_cap(provider):
            return provider
        note_for = getattr(provider, "act_cap_note", None)
        note = note_for() if callable(note_for) else None
        if note and note not in self._told:  # once per run, not per call
            self._told.add(note)
            self.log(f"warning: {note}")
        return CallCapped(provider, lambda: self.budget.config.max_usd - self.budget.state.usd)

    def _ai_room(self) -> bool:
        """Whether one more AI call fits in what is left of `budget.max_usd`
        (`BudgetGuard.room_for_a_call`). The first time it does not, the run's
        log says so and the search ends after its round: no more AI changes are
        written, and the run goes to its final check."""
        verdict = self.budget.room_for_a_call()
        if verdict.allowed:
            return True
        if self._ai_out is None and (verdict.reason or "").startswith(AI_USED_UP):
            self._ai_out = verdict.reason
            self.log(f"{verdict.reason}; no more AI changes: the round ends and the run goes to its final check")
        return False

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

    def _make_source(self, block: str, candidate_id: str = "") -> str:
        """A skeleton's candidate: its module's text. A tree's: the path of its
        folder (`{tree}`), which the cascade makes when it evaluates it."""
        if self.repo is not None:
            from evolvekit import tree

            folder = tree.tree_dir(self.ledger.run_dir, candidate_id)
            return str(folder / self.repo.prefix if self.repo.prefix else folder)
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
        alive = [c for c in self.archive if _competes(c)]
        return sorted(alive, key=lambda c: (-(c.fitness or 0.0), c.id))[:k]

    def _economics(self) -> list[GenerationPoint]:
        """The cost-per-progress series as of right now.

        Rebuilt from the two logs rather than accumulated, for the same reason
        the archive is: the logs are the record, and a series that drifts from
        them would be worse than no series. Read as the replay reads them
        (`_replayed_rows`), so the stop rules see the order the archive has --
        the rows as written, with the best's re-evaluations as of the round
        each was made in (`economics.series`, the series `status` reads).
        """
        return series(
            self._replayed_rows(self.ledger.runs(), []),
            self.ledger.usage(),
            window=self.config.stop.economics_window,
            # Each round against its own bar, as the stop policy judged it --
            # also when `_restore_stop_policy` replays the series before the
            # policy has a best (`epsilon_now` would then miss `1e-9·|best|`).
            epsilon=self.stop_policy.epsilon_at,
            reevaluations=self._reevaluations,
            holdout_penalty=self.config.evaluate.holdout_penalty,
        )

    def _refresh_noise(self) -> None:
        """The sd of one finished candidate's score at the final stage, as the
        run's noise estimate stands now (`Noise.score_sd`: paired on a
        repeatable stage, so 0 at `seeds: 1`), handed to the two rules that
        read it: the stop policy's automatic epsilon and the elite rule, with
        the degrees of freedom it rests on (`Noise.score_df`), which widen
        each rule's normal quantile to Student's t (Decision 17b)."""
        final = self.config.final_stage
        sd = self.cascade.noise.score_sd(final.id, self.config.evaluate.score, final.seeds)
        df = self.cascade.noise.score_df(final.id, self.config.evaluate.score)
        self.stop_policy.noise_sd, self.stop_policy.noise_df = sd, df
        self.grid.noise_sd, self.grid.noise_df = sd, df

    def _archive_scores(self) -> list[float]:
        return [c.fitness for c in self.archive if _competes(c)]

    def _archive_stage_scores(self) -> dict[str, list[float]]:
        """Per command stage, the competing archive members' own score there:
        what that stage's `promote.archive_percentile` compares a candidate
        with (like with like). A member recorded without it is left out."""
        members = [c for c in self.archive if _competes(c)]
        return {
            stage.id: [c.stage_scores[stage.id] for c in members if stage.id in c.stage_scores]
            for stage in self.config.evaluate.stages
            if stage.kind == "command"
        }

    # -- resume ----------------------------------------------------------

    def _replayed_rows(self, raw: list[dict], reevaluations: Sequence[dict] | None = None) -> list[dict]:
        """`runs.jsonl` rows judged by today's rules: pooled with the run's
        re-evaluations (`leaderboard.pool_rows`; `reevaluations`, the run's
        when not given), `competes` settled, the hold-out veto applied against
        the seed's private score, and the ranking score recomputed with this
        run's λ. Pooling comes first, as in `leaderboard.read_rows`; the veto
        reads the private score only, which pooling and λ leave alone."""
        reference = seed_private(raw)
        pooled = pool_rows(
            raw, self._reevaluations if reevaluations is None else reevaluations,
            holdout_penalty=self.config.evaluate.holdout_penalty,
        )
        return [self._rerank(self._settle_competes(row, reference)) for row in pooled]

    def resume(self) -> int:
        """Rebuild the archive from `runs.jsonl`. Returns the last generation.

        `archive.json` is only ever a snapshot; the JSONL is the record. A run
        that died between the last append and the last snapshot still comes
        back consistent, which is the whole reason the rebuild reads the log.
        """
        raw = self.ledger.runs()
        rows = self._replayed_rows(raw)
        self.cascade.seed_private = seed_private(raw)
        self.cascade.seed_public = seed_public(raw)
        if not rows:
            return 0
        self._refresh_noise()
        self.grid = Archive.from_records(
            rows,
            self.config.search.archive,
            tree=self.repo is not None,
            noise_sd=self.stop_policy.noise_sd,
            noise_df=self.stop_policy.noise_df,
            space=self.config.problem.parameters,
            temperature=self.config.search.selection_temperature,
        )
        last_generation = 0
        for row in rows:
            candidate = Candidate.from_record(row)
            candidate.source = self._make_source(candidate.block, candidate.id)
            self.archive.append(candidate)
            self.by_id[candidate.id] = candidate
            if not candidate.rejected:
                self._embed_context = (candidate.id, int(candidate.generation))
                self.novelty.add(candidate.id, candidate.block, parent_block=self._parent_block(candidate))
            # A twin's hash and numbers at the stage that caught it never became
            # a target in the run that wrote the row, so they do not become one
            # now (a noise twin's hash is its own, and would). At its earlier
            # stages it was added like anyone else.
            caught_at = candidate.stages_reached[-1] if candidate.behaviour_twin_id and candidate.stages_reached else None
            for stage_id, signature in (candidate.behaviour_signatures or {}).items():
                if stage_id != caught_at:
                    self.behaviour.add(candidate.id, stage_id, signature, generation=int(candidate.generation))
            # The numbers the noise rule compares, with the stage score. A row
            # from before them is a target by its hash only.
            for stage_id, raw_values in (candidate.behaviour_values or {}).items():
                values = Behaviour.from_json(raw_values)
                if values is not None and stage_id != caught_at:
                    self.behaviour.add(candidate.id, stage_id, "", values, generation=int(candidate.generation))
            if candidate.novelty == "behavioural":
                self._remember_dead_end(candidate)
            last_generation = max(last_generation, int(candidate.generation))
            suffix = candidate.id.rsplit("-c", 1)[-1]
            if suffix.isdigit():
                self._counter = max(self._counter, int(suffix))
        self._resumed_generation = last_generation
        self._restore_budget(rows)
        self._replay_missing_step()
        self._replay_missing_screen_checks(last_generation)
        return last_generation

    def _settle_competes(self, row: dict, seed_private: float | None = None) -> dict:
        """Judge a row written before `competes` existed by today's rule,
        hold-out veto included (`seed_private`: the seed's private score).

        Without this a resumed older run would put its failed and proxy-only
        candidates straight back into the archive, which is the defect the
        field exists to end. A row that carries `competes` keeps it, and the
        veto is applied on top exactly as in a live run: a row written before
        the veto existed, whose hold-out is below the seed's, stops competing.
        Each row is judged with the noise margin it was judged with live
        (`holdout_margin`); a row from before the margin, literally.
        """
        if "competes" in row:
            finished = bool(row["competes"])
        else:
            finished = finished_final_stage(
                self.config,
                rejected=bool(row.get("rejected")),
                last_failure=row.get("last_failure"),
                stages_reached=row.get("stages_reached") or [],
                private_score=row.get("private_score"),
                gated=row.get("gated"),
            )
        margin = float(row.get("holdout_margin") or 0.0)  # none on a row from before A2: the literal rule
        veto = holdout_veto(self.config, row.get("private_score"), seed_private, margin) if finished else None
        if veto is not None:
            return {**row, "competes": False, "veto": veto}
        if "competes" in row:
            return row
        return {**row, "competes": finished}

    def _rerank(self, row: dict) -> dict:
        """The ranking score again, with this run's `evaluate.holdout_penalty`.

        A row written before the default became 0 stored a ranking score at
        λ = 1; kept as it is, it would sit in one archive beside rows ranked by
        the public score, two scales in one order. Rebuilt as
        `score − λ·max(0, score − private_score)`, or `score` when the row has
        no private score.
        """
        score = row.get("score")
        if score is None:
            return row
        return {
            **row,
            "ranking_score": ranking_score(
                float(score), float(score), row.get("private_score"), self.config.evaluate.holdout_penalty
            ),
        }

    # -- the loop --------------------------------------------------------

    def run(self, generations: int | None = None) -> RunSummary:
        """Take the run lock, then search. One writer per run directory."""
        with run_lock(self.ledger.run_dir) as lock:
            # Before the first event of this session: a refusal must not leave a
            # "crashed" session behind in a directory it declined to touch.
            self._check_same_problem()
            recorded = read_events(self.ledger.run_dir)
            self._durations = _generation_durations(recorded)
            self._seed_stages = _seed_stage_durations(recorded)
            # The time limit is the run's: earlier sessions' hours count.
            self.run_stop.start(used_s=active_seconds(recorded))
            self.heartbeat.start(phase="starting")
            summary: RunSummary | None = None
            try:
                if lock.reclaimed_from:
                    self.log(
                        f"reclaimed a stale lock from dead pid {lock.reclaimed_from}"
                    )
                summary = self._run(generations)
            except KeyboardInterrupt:
                self.events.emit("run_interrupted")
                self.heartbeat.stop(phase="interrupted")
                raise
            except BaseException as exc:
                # Written before it propagates: the traceback goes to a terminal
                # that may not exist; this goes where `status` can find it.
                self.events.emit("run_crashed", error=f"{type(exc).__name__}: {exc}")
                self.heartbeat.stop(phase="crashed")
                raise
            finally:
                self.run_stop.close()
                if self.repo is not None and summary is None:
                    # A crash or Ctrl+C skips `_finalise`, and a tree left
                    # behind stays registered in the engineer's repository.
                    # Best effort: it never hides the real error.
                    try:
                        self._remove_trees()
                    except Exception as exc:  # noqa: BLE001
                        print(f"evolvekit: the run's trees were not all removed ({exc})", file=sys.stderr)
                if self.repo is not None and self.config.problem.tree.build:
                    # The search built candidates into the shared interpreter:
                    # it is pointed back at the engineer's checkout (decision 31).
                    restore_build(self.config.problem, log=self.log)
            best = summary.best
            self.events.emit(
                "run_finished",
                stop_reason=summary.stop_reason,
                generations=summary.generations,
                candidates=summary.candidates,
                seed_score=summary.seed_score,
                best_id=best.id if best else None,
                best_score=best.score if best else None,
                best_fitness=best.fitness if best else None,
            )
            self.heartbeat.stop(phase="finished")
            return summary

    def _run(self, generations: int | None) -> RunSummary:
        summary = RunSummary()

        started_at = self.resume()
        # `search.generations` is the plan for the run *directory*: a run that
        # died in generation 9 of 12 is resumed to finish the 12, not to start
        # another 12. An explicit count (`--generations K`) means "K more".
        planned = self.config.search.generations
        total = generations if generations is not None else max(0, planned - started_at)
        if generations is None and started_at and total == 0:
            summary.stop_reason = (
                f"the run directory already holds its {planned} planned generation(s); "
                "`--generations K` runs K more"
            )
        self.events.emit(
            "run_started",
            **self._describe_run(first_generation=started_at + 1, planned=total),
        )
        for warning in race_warnings(self.config):
            self.log(f"warning: {warning}")
        # Not `if started_at`: a run stopped before generation 1 finished holds
        # only its seed, and the seed's generation is 0.
        seed = None
        if self.archive:
            self.log(
                f"resumed {len(self.archive)} candidate(s) from runs.jsonl "
                f"({self.grid.occupancy()})"
            )
            summary.candidates = len(self.archive)
            recorded = self._recorded_seed()
            if recorded is None or not self._seed_failed(recorded):
                summary.seed_score = recorded.score if recorded else None
            else:
                # The last session aborted on this seed, or bred past a seed
                # that never got through. Running the same command again is
                # what a retrying wrapper does; it must not step over the abort
                # and breed against a harness that may still be broken. The
                # seed is evaluated again first -- the evaluator may have been
                # fixed in between -- and judged exactly as a fresh run's is.
                self._forget_behaviour(recorded.id)
                seed = self._seed_candidate()
                self.log(
                    f"gen 0  the recorded seed {recorded.id} did not get through the cascade; "
                    "evaluating it again before anything is bred"
                )
        else:
            seed = self._seed_candidate()
        if seed is not None:
            halt = self.run_stop.poll()
            if halt is not None:
                summary.stop_reason = halt
                self.log(f"gen 0  not started: {halt}")
                return self._finalise(summary)
            began = self._generation_started(0, children=0)
            if self._evaluate_and_record([seed], generation=0):
                summary.stop_reason = self.run_stop.reason or "stopped"
                self.log(
                    f"gen 0  cut short: {summary.stop_reason}. The seed is evaluated again when the "
                    "run is resumed"
                )
                return self._finalise(summary)
            self._generation_finished(0, began, [seed])
            summary.seed_score = seed.score
            summary.candidates += 1
            self.log(f"gen 0  seed        score={_fmt(seed.score)}")

            # A seed that cannot get through the cascade means the harness is
            # broken, not the heuristic. Stop here, before the first LLM call,
            # so a misconfigured evaluator never costs money.
            if self._seed_failed(seed):
                detail = (seed.reject_reason or seed.last_failure or "scored failure_score").strip()
                first_line = detail.splitlines()[0] if detail else "unknown failure"
                summary.stop_reason = (
                    "seed failed evaluation — fix the harness before spending: "
                    f"{first_line}"
                )
                if seed.gated and not (seed.rejected or seed.last_failure):
                    # Every candidate is judged against the seed, and a seed
                    # outside the gates is not a starting point anybody wants.
                    summary.stop_reason = (
                        f"the seed breaks a gate: {seed.gated} (evaluate.gates). Nothing was "
                        "searched: change the starting point or the gate, then run again"
                    )
                summary.aborted = True
                self.log(f"ABORT: {summary.stop_reason}")
                return self._finalise(summary)

        # The replay reads the noise as it is now (open question 12).
        self._refresh_noise()
        decision = self._restore_stop_policy()

        for offset in range(1, total + 1):
            generation = started_at + offset
            verdict = self.budget.check()
            if not verdict.allowed:
                summary.stop_reason = verdict.reason or "budget exhausted"
                break
            if self.config.search.uses_llm and not self._ai_room() and self._ai_out:
                summary.stop_reason = self._ai_out
                break
            if decision.stop:
                summary.stop_reason = decision.reason or "stop policy"
                break
            capped = self.budget.check_full_eval()
            if not capped.allowed:
                # Breeding on would pay for children -- model calls, cheap
                # stages -- none of which can reach the stage that counts.
                summary.stop_reason = (
                    f"{capped.reason}: no candidate can reach the final stage today. Run the same "
                    "command again tomorrow, or raise the cap -- it counts candidates admitted to "
                    "the final stage per calendar day"
                )
                break
            halt = self.run_stop.before_generation(
                self._projected_s(), first=not any(g >= 1 for g in self._durations))
            if halt is not None:
                summary.stop_reason = halt
                break

            self._maybe_refresh_scratchpad(generation)
            self._refresh_noise()

            began = self._generation_started(
                generation, children=self.children_per_generation, plateau=decision.plateau
            )
            # Last generation's pressure on its parents counts half now
            # (`Archive.from_records` replays the same on resume).
            self.grid.decay()
            children = self._adopt_pending(generation, plateau=decision.plateau)
            if children is None:
                children = self._breed(generation, plateau=decision.plateau)
                self._write_pending(generation, children)
            viable = [c for c in children if not c.rejected]
            for child in viable:
                # Handed to the cascade: a refused or failed child cost its
                # parent nothing and does not make it less attractive.
                if child.parent_id:
                    self.grid.note_evaluated(child.parent_id)
            if self._evaluate_and_record(viable, generation=generation):
                # Stopped in the middle: nothing of this generation is
                # recorded, and `pending.json` keeps its children for the
                # resumed run, which finishes it first.
                summary.stop_reason = self.run_stop.reason or "stopped"
                self.log(
                    f"gen {generation:<2} cut short: {summary.stop_reason}. Its children stay in "
                    "pending.json: the resumed run finishes this generation first"
                )
                break
            summary.candidates += len(children)
            for child in children:
                if child.rejected and child.id not in self.results:
                    self._record(child)
            # The local step's size, from this round's local steps as they
            # measured (before a re-evaluation pools the best in place).
            self._adapt_step(generation, children)
            self._pending_path.unlink(missing_ok=True)
            summary.generations = offset
            # The round is judged by what its own candidates measured; a
            # re-evaluation of the best at its end is never progress: it only
            # moves the bar to the pooled best (`StopPolicy.rebase`).
            # (A float, taken now: the re-evaluation pools the best in place.)
            measured = self.best.fitness if self.best is not None else None
            rebased = self._reevaluate_best(generation)
            # The stop policy first, so the generation's event carries the
            # flat count and the epsilon `status` reads.
            points = self._economics()
            point = points[-1] if points else None
            decision = self.stop_policy.update(measured, point)
            best = self.best
            if rebased and best is not None:
                self.stop_policy.rebase(best.fitness)
            self._generation_finished(generation, began, children)

            if self._provider_is_dead():
                summary.stop_reason = (
                    f"provider failing ({self._provider_failures} consecutive errors): "
                    f"{self._provider_halt}"
                )
                summary.aborted = True
                self.log(f"ABORT: {summary.stop_reason}")
                break

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
            if self._ai_out:
                summary.stop_reason = self._ai_out
                break
            if decision.stop:
                summary.stop_reason = decision.reason or "stop policy"
                break

        return self._finalise(summary)

    # -- re-evaluating the best (search.reevaluate_best_every) ------------

    def _reevaluate_best(self, generation: int) -> bool:
        """After round `generation` is recorded, every `reevaluate_best_every`
        rounds: the archive's best runs once more at the final stage on seed
        `REEVALUATION_SEED_BASE + k` (k: the run's re-evaluations so far), the
        run goes to `reevaluations.jsonl`, and its runs are pooled everywhere --
        the candidate, the noise (`Noise.pool`), the archive (`Archive.update`);
        the caller moves the stop policy's bar (`StopPolicy.rebase`) once the
        round is judged. Skipped, with a line in the log, when the daily cap or
        the time left says no. True when a re-evaluation was made."""
        every = self.config.search.reevaluate_best_every
        if every <= 0 or generation < 1 or generation % every:
            return False
        best = self.best
        if best is None:
            return False
        final = self.config.final_stage
        seed = REEVALUATION_SEED_BASE + len(self._reevaluations)
        why = None
        capped = self.budget.check_full_eval()
        left, needed = self.run_stop.left_s(), self._reevaluation_s()
        if not capped.allowed:
            why = capped.reason
        elif self.run_stop.poll() is not None:
            why = self.run_stop.reason
        elif left is not None and needed is not None and left < needed:
            why = f"{left:.0f} s of the run's time is left, less than one run of it ({needed:.0f} s)"
        # Scored over the measures as first written: the earlier stages' are in
        # the row, and this run's replace the final stage's.
        written = next((r for r in reversed(self.ledger.runs()) if r.get("id") == best.id), None)
        outcome = None if why else self.cascade.reevaluate(
            best, seed, earlier_kpis=dict((written or {}).get("kpis") or best.kpis))
        if outcome is None or not outcome.ok or outcome.abandoned or outcome.score is None:
            if why is None:
                why = (outcome.failure if outcome is not None and outcome.failure else "the run was stopped")
            self.log(f"gen {generation:<2} the best {best.id} was not run again on seed {seed}: {why}")
            return False
        prior = int(best.replicates or 0) or final.seeds
        entry = {
            "id": best.id,
            "generation": generation,
            "seed": seed,
            "stage": final.id,
            "prior_runs": prior,
            "kpis": dict(outcome.kpis),
            "score": float(outcome.score),
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        # The noise first: it remembers the runs it pooled (`Noise.pool`), so a
        # run that dies before the entry is written makes the same run on the
        # same seed after the resume without pooling it twice; the other way
        # round, the entry would stand in `reevaluations.jsonl` with its run
        # missing from the noise for good.
        self.cascade.noise.pool(final.id, best.id, outcome)
        self.cascade.noise.save()
        self.ledger.record_reevaluation(entry)
        self._reevaluations.append(entry)
        was = best.fitness
        (pooled,) = pool_rows([best.to_record()], [entry], holdout_penalty=self.config.evaluate.holdout_penalty)
        best.kpis = dict(pooled["kpis"])
        best.score = pooled["score"]
        best.public_score = pooled.get("public_score")
        best.ranking_score = pooled["ranking_score"]
        best.generalization_gap = pooled.get("generalization_gap")
        best.replicates = int(pooled["replicates"])
        self._refresh_noise()
        self.grid.update(best)
        self.log(
            f"gen {generation:<2} the best {best.id} run again on seed {seed}: {_fmt(float(outcome.score))}; "
            f"the mean of its {best.replicates} runs is {_fmt(best.fitness)} (was {_fmt(was)})"
        )
        return True

    def _reevaluation_s(self) -> float | None:
        """How long one run of the final stage takes: the seed's final stage
        over its seeds; `None` before the seed has passed it."""
        final = self.config.final_stage
        spent = self._seed_stages.get(final.id)
        return None if spent is None else spent / max(1, final.seeds)

    def _recorded_seed(self) -> Candidate | None:
        """The seed as last evaluated: a seed evaluated again after an abort is
        a second seed row, and the later one is the one that counts."""
        return next((c for c in reversed(self.archive) if c.operator == SEED_OPERATOR), None)

    def _seed_failed(self, seed: Candidate) -> bool:
        return bool(
            seed.rejected
            or seed.last_failure
            or seed.gated
            or seed.score == self.config.evaluate.failure_score
        )

    def _forget_behaviour(self, candidate_id: str) -> None:
        """Drop a candidate's behaviour signatures, so that the seed evaluated
        again is not stopped as a twin of its own failed first attempt."""
        self.behaviour.by_key = {
            key: owner for key, owner in self.behaviour.by_key.items() if owner != candidate_id
        }
        for entries in self.behaviour.values.values():
            entries.pop(candidate_id, None)
        self.behaviour.generations.pop(candidate_id, None)

    # -- a generation that was interrupted -------------------------------

    @property
    def _pending_path(self) -> Path:
        return self.ledger.run_dir / "pending.json"

    def _write_pending(self, generation: int, children: Sequence[Candidate]) -> None:
        """Write a generation's children down before they cost anything to evaluate.

        A generation is only recorded once its whole cascade has returned --
        hours, for a slow solver. If the run dies in between, the children (and
        the model calls that bred them) would be gone, and the resumed run would
        breed different ones whose evaluations start from nothing. Written
        down, they are evaluated again under the same ids, and every run that
        had finished is found in the evaluation cache.
        """
        payload = {"generation": generation, "children": [c.to_record() for c in children]}
        scratch = self._pending_path.with_suffix(".tmp")
        scratch.write_text(json.dumps(payload), encoding="utf-8")
        scratch.replace(self._pending_path)

    def _adopt_pending(self, generation: int, *, plateau: bool = False) -> list[Candidate] | None:
        """The children `pending.json` holds for `generation`, or `None`.

        Adopted children skip `_plan_operators` and `_breed_one`, so their big
        step is counted here, as the replay counts a recorded one (`plateau`: what
        this round is planned with)."""
        children = self._read_pending(generation)
        for child in children or []:
            if str(child.operator).startswith("big_step"):
                self._count_big_step(
                    generation, tried=True, applied=self._big_step_applied(child), plateau=plateau
                )
        return children

    def _read_pending(self, generation: int) -> list[Candidate] | None:
        try:
            payload = json.loads(self._pending_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if payload.get("generation") != generation:
            self._pending_path.unlink(missing_ok=True)  # left by a generation that was recorded after all
            return None
        children: list[Candidate] = []
        for record in payload.get("children") or []:
            try:
                child = Candidate.from_record(record)
            except (BlockError, TypeError):
                continue
            if child.id in self.by_id:
                continue  # recorded before the run died
            child.source = self._make_source(child.block, child.id)
            suffix = child.id.rsplit("c", 1)[-1]
            if suffix.isdigit():
                self._counter = max(self._counter, int(suffix))
            children.append(child)
        if not children:
            return None
        self.events.emit("generation_adopted", generation=generation, children=len(children))
        self.log(
            f"gen {generation:<2} picking up {len(children)} child(ren) bred before the run "
            "was interrupted; their finished evaluations are looked up, not run again"
        )
        return children

    def _finalise(self, summary: RunSummary) -> RunSummary:
        summary.best = self.best
        if self.repo is not None:
            self._remove_trees()
        summary.rejected = sum(1 for c in self.archive if c.rejected)
        summary.no_ops = self.grid.no_ops
        summary.duplicates = self.grid.duplicates
        summary.behavioural = sum(
            1 for c in self.archive if c.novelty == "behavioural"
        )
        summary.near = sum(1 for c in self.archive if c.novelty == "near")
        summary.unfinished = sum(
            1 for c in self.archive if not c.rejected and not c.competes and not c.veto
        )
        summary.vetoed = sum(1 for c in self.archive if not c.rejected and c.veto)
        summary.embedding_calls = self.ledger.embedding_calls
        summary.cells = len(self.grid.cells)
        summary.occupancy = self.grid.occupancy()
        summary.totals = self.ledger.totals()
        summary.economics = self._economics()
        summary.children_per_generation = self.children_per_generation
        self._snapshot_archive()
        return summary

    def _remove_trees(self) -> None:
        """Every tree of the run, the best's included: the safety net behind
        `_drop_trees` (a crash, a stop in the middle of a round). The final
        check makes its own, and `study export --diff` and `study pr` rebuild
        the change from the recorded block. A tree still held open by another
        program stays, and is said so once; never an error."""
        from evolvekit import tree

        tree.remove_all(self.ledger.run_dir)
        root = tree.tree_dir(self.ledger.run_dir, "-").parent
        if not root.is_dir():
            return
        for folder in sorted(p for p in root.iterdir() if p.is_dir()):
            self.log(f"a combination's folder is still in use and stays: {folder}")

    # -- resume: what the run directory has already used up -------------

    def _restore_budget(self, rows: list[dict]) -> None:
        """Charge the guard with what this run directory has already spent.

        The caps are promises about a run, and a run is a directory: it is
        resumed by running the same command again, after a crash, a halt or a
        budget stop. A guard that started from zero each time turned
        `budget.max_usd` into "per invocation", and re-running a budget-stopped
        run simply bought the allowance again.
        """
        for usage in self.ledger.usage():
            self.budget.spend(
                usd=float(usage.get("usd", 0.0) or 0.0),
                tokens=int(usage.get("input_tokens", 0) or 0)
                + int(usage.get("output_tokens", 0) or 0),
            )
        # A full evaluation is metered when the final stage is *run*, whether or
        # not it succeeds. `created_at` is when the candidate was bred, which is
        # the closest thing to a timestamp a row carries; a candidate bred just
        # before midnight UTC and evaluated just after is counted a day early.
        final = self.config.final_stage.id
        for row in rows:
            failure = str(row.get("last_failure") or "")
            ran_and_failed = (
                failure.startswith(f"stage {final}: ") and "cap reached" not in failure
            )
            day = str(row.get("created_at") or "")[:10]
            if day and (final in (row.get("stages_reached") or []) or ran_and_failed):
                self.budget.record_full_eval(day)
        # A re-evaluation of the best is a full evaluation too, on the day it ran
        # (one that failed wrote no row, and is not counted: a small undercount).
        for entry in self._reevaluations:
            day = str(entry.get("at") or "")[:10]
            if day:
                self.budget.record_full_eval(day)

    def _restore_stop_policy(self) -> StopDecision:
        """Replay the recorded generations through the stop policy.

        Patience is a property of the run as well: a run that stopped after
        eight flat generations has not earned eight more by being started
        again. Replaying the series, big steps included, leaves the policy
        exactly where the last process left it -- and on a fresh run the series
        is the seed generation alone, which is what the policy was always fed
        first.

        Two counts, as live: every big-step row was tried (planned), and it
        applied unless it has the shape `_breed_one` gives a failed apply --
        rejected, no novelty verdict, and its parent's block written back. An
        applied one planned on the plateau reopens the spacing window
        (`_plateau_step_at`), counted before that round's update, as live.
        """
        tried: set[int] = set()
        applied: set[int] = set()
        for c in self.archive:
            if not str(c.operator).startswith("big_step"):
                continue
            tried.add(int(c.generation))
            if self._big_step_applied(c):
                applied.add(int(c.generation))
        # The plateau each round was planned with, as recorded (exact on a noisy
        # run whose noise moved since); a run from before it was recorded falls
        # back on the policy's own state, which is where it stood when that round
        # was planned while the noise is unchanged.
        recorded = _generation_plateaus(read_events(self.ledger.run_dir))
        decision = StopDecision(False)
        for point in self._economics():
            if point.generation in tried:
                plateau = recorded.get(point.generation, self.stop_policy.plateau)
                self._count_big_step(
                    point.generation, tried=True, applied=point.generation in applied,
                    plateau=plateau,
                )
            # As live: the round by its own candidates, then the bar moved to
            # the pooled best where the best was re-evaluated at its end.
            decision = self.stop_policy.update(point.measured_best, point)
            if point.rebased:
                self.stop_policy.rebase(point.best)
        return decision

    def _big_step_applied(self, child: Candidate) -> bool:
        """A big-step row applied unless it has the shape `_breed_one` gives a
        failed apply: rejected, no novelty verdict, its parent's block written
        back. A missing parent counts it as applied; it cannot occur today (a
        big step's parent is the archive best, which is in the archive)."""
        parent = self.by_id.get(child.parent_id) if child.parent_id else None
        return not (
            child.rejected
            and child.novelty is None
            and parent is not None
            and child.block == parent.block
        )

    # -- generation internals -------------------------------------------

    def _generation_started(self, generation: int, *, children: int, plateau: bool = False) -> float:
        self._generation_now = generation
        # `plateau`: what the round was planned with, read back by `_restore_stop_policy`.
        self.events.emit(
            "generation_started", generation=generation, children_planned=children,
            plateau=bool(plateau),
        )
        self.heartbeat.update(
            phase="breeding" if children else "evaluating",
            generation=generation,
            candidate_id=None,
            stage=None,
        )
        return time.perf_counter()

    def _generation_finished(
        self, generation: int, began: float, children: Sequence[Candidate]
    ) -> None:
        best = self.best
        duration = round(time.perf_counter() - began, 3)
        self._durations[generation] = duration
        self.events.emit(
            "generation_finished",
            generation=generation,
            duration_s=duration,
            children=len(children),
            rejected=sum(1 for c in children if c.rejected),
            best_id=best.id if best else None,
            best_fitness=best.fitness if best else None,
            spent_usd=self.budget.state.usd,
            flat_generations=self.stop_policy.stagnant,
            stop_epsilon=self.stop_policy.epsilon_now(),
        )
        self._snapshot_archive()

    def _snapshot_archive(self) -> None:
        """`archive.json`, refreshed. It used to be written once, when a session
        ended: blank for the whole of a live run, and never written at all by a
        run that was killed."""
        payload = self.grid.to_dict()
        payload["economics_window"] = self.config.stop.economics_window
        try:
            self.ledger.write_archive(payload)
        except OSError as exc:
            # A view of `runs.jsonl`, rebuilt from it on every resume: one that
            # cannot be refreshed right now is not worth the run.
            print(f"evolvekit: archive.json not refreshed ({exc}); the run goes on", file=sys.stderr)

    def _seed_candidate(self) -> Candidate:
        candidate_id = self._next_id(0)
        return Candidate(
            id=candidate_id,
            generation=0,
            block=self.seed_block,
            source=self.skeleton_source if self.repo is None else self._make_source("", candidate_id),
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

    def _projected_s(self) -> float | None:
        """How long the next generation may take, for `budget.max_hours`: the
        longest of the last two. With only the seed done, from the seed's
        stages (`_first_round_s`); without them, the seed's duration times the
        breadth -- an upper bound, far above a round when a cheaper stage
        screens first, since then most children never run the expensive one."""
        done = sorted(generation for generation in self._durations if generation >= 1)
        if done:
            return max(self._durations[generation] for generation in done[-2:])
        seed = self._durations.get(0)
        if seed is None:
            return None
        first = self._first_round_s(seed)
        return seed * self.children_per_generation if first is None else first

    def _first_round_s(self, seed_s: float) -> float | None:
        """The first round as the plan counts one (`harness/plan.py`), from what
        the seed took: in each command stage, one wave of runs is the seed's time
        there over its waves (its runs on the stage's workers), and the children
        make `ceil(candidates * runs / workers)` waves -- every child on the
        first stage, at most `top_k_per_generation` after a stage promoting only
        so many. The rest of the seed's time (static checks, a build, the
        bookkeeping) is paid once per child, and so is the AI's time
        (`ai_seconds`). `None` when the seed's stages are not on record (a run
        directory from before they were read)."""
        stages = [stage for stage in self.config.evaluate.stages if stage.kind == "command"]
        if not stages or any(stage.id not in self._seed_stages for stage in stages):
            return None
        children = self.children_per_generation
        total, reaching = 0.0, children
        for stage in stages:
            runs = max(1, len(stage.instances)) * max(1, stage.seeds)
            workers = max(1, stage.workers)
            wave = self._seed_stages[stage.id] / math.ceil(runs / workers)
            total += math.ceil(reaching * runs / workers) * wave
            promote = stage.promote
            if promote.top_k_per_generation is not None and promote.archive_percentile is None:
                reaching = min(reaching, promote.top_k_per_generation)
        rest = max(0.0, seed_s - sum(self._seed_stages[stage.id] for stage in stages))
        return total + children * (rest + ai_seconds(self.config.search.operators)[0])

    def _plan_operators(self, generation: int, plateau: bool) -> list[str]:
        """One operator name per child. The big step, when due, takes slot 0.

        A big step is due on the schedule, or on the plateau at most once per
        `big_step_every` generations after one applied (Q8: on a plateau every
        generation spent a strong-model call), see `_plateau_step_allowed`."""
        count = self.children_per_generation
        weights = self.config.search.operators
        sweepable = self.config.problem.parameters is not None or has_params(self.seed_block)
        names = [
            n
            for n, w in sorted(weights.items())
            if w > 0 and (n != "param_lhs" or sweepable)
        ]
        shares = [weights[n] for n in names]
        if not names:  # only param_lhs configured, against a param-less skeleton
            if not self.config.search.uses_llm:
                raise ValueError(
                    "search.operators gives a share only to `param_lhs`, but the seed "
                    "block declares no `# PARAMS: {...}` line, so there is nothing to "
                    "sweep. Declare the block's tunable constants, or give an LLM "
                    "operator a share (and configure `models`)"
                )
            names, shares = ["rewrite"], [1.0]

        plan = [self.rng.choices(names, weights=shares, k=1)[0] for _ in range(count)]
        every = self.config.search.big_step_every
        scheduled = every > 0 and generation % every == 0
        self._big_step_reason = None
        self._big_step_on_plateau = plateau
        if self.config.search.takes_big_steps and (
            scheduled or (plateau and self._plateau_step_allowed(generation))
        ):
            plan[0] = "big_step"
            # Tried counts here, for `min_big_steps`; applied counts in `_breed_one`.
            self._count_big_step(generation, tried=True, applied=False, plateau=plateau)
            self._big_step_reason = big_step_reason(
                scheduled=scheduled, every=every, stagnant=self.stop_policy.stagnant,
                previous=self._previous_big_step(),
            )
        return plan

    def _plateau_step_allowed(self, generation: int) -> bool:
        """Whether the plateau may buy a big step this generation (Q8: on a
        plateau every generation spent a strong-model call): once one applied,
        the next waits `big_step_every` generations -- unless the search
        improved since, or the plateau is held open by `stop.min_big_steps`
        (the run cannot stop before a big step is tried, so it does not wait)."""
        at = self._plateau_step_at
        if at is None or generation - at >= self.config.search.big_step_every:
            return True
        policy = self.stop_policy
        if generation - 1 - policy.stagnant >= at:
            return True  # the last improving generation is at or after it
        stop = self.config.stop
        return policy.stagnant >= stop.patience and policy.big_steps_in_window < stop.min_big_steps

    def _count_big_step(self, generation: int, *, tried: bool, applied: bool, plateau: bool) -> None:
        """A0's two counts and the plateau's spacing, from one place: planning
        and applying live, the replay on resume, and an adopted `pending.json`
        generation. A big step tried counts for `min_big_steps`; one that applied
        counts for the economics guard and, planned on the plateau, opens the
        spacing window."""
        if tried:
            self.stop_policy.note_big_step()
        if applied:
            self.stop_policy.note_big_step_applied()
            if plateau:
                self._plateau_step_at = generation

    def _previous_big_step(self) -> LedgerEntry | None:
        """The run's latest big step (applied or not) as its ledger line, for the hint."""
        steps = [c for c in self.archive if str(c.operator).startswith("big_step")]
        if not steps:
            return None
        row = max(steps, key=lambda c: (_id_counter(c), c.id))
        best = self.best
        return entry_for(
            row, self.by_id.get(row.parent_id) if row.parent_id else None,
            best_id=best.id if best else None,
            twin=self.by_id.get(row.behaviour_twin_id) if row.behaviour_twin_id else None,
            final_stage=self.config.final_stage.id, tree=self.repo is not None,
        )

    # -- tuning a declared space: who is worth starting from -------------

    TUNING_ELITES = 4
    """A local step starts from one of the best few, not always from the best:
    on a noisy objective the single best is partly luck, and a search that only
    ever looks around one lucky configuration has bet everything on it."""

    def _tuning_elites(self) -> list[Candidate]:
        ranked = sorted(
            (c for c in self.archive if c.competes and c.params and c.fitness is not None),
            key=lambda c: -c.fitness,
        )
        return ranked[: self.TUNING_ELITES]

    def _tuning_parent(self, fallback: Candidate) -> Candidate:
        elites = self._tuning_elites()
        if not elites:
            return fallback
        weights = [0.5 ** rank for rank in range(len(elites))]  # 1, 1/2, 1/4, 1/8
        return self.rng.choices(elites, weights=weights, k=1)[0]

    def _mate_for(self, parent: Candidate) -> Candidate | None:
        others = [c for c in self._tuning_elites() if c.id != parent.id and c.params != parent.params]
        return self.rng.choice(others) if others else None

    # -- the local step's size: the 1/5 rule (`work/perturb.json`) ----------

    STEP_LIMITS = (0.03, 0.4)
    """The smallest and the largest local step, as a share of the range."""
    STEP_FACTOR = 1.2
    STEP_SUCCESS = 0.2
    """The success rate the step balances at (Rechenberg's 1/5 rule): above
    it the step grows, below it the step shrinks."""

    def _perturb_path(self) -> Path:
        return self.ledger.run_dir / "work" / "perturb.json"

    def _read_perturb(self) -> tuple[float, list[dict]]:
        """`work/perturb.json`'s step size and history; the default size and no
        history for a run written before it, or when it is malformed."""
        try:
            data = json.loads(self._perturb_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return STEP_SCALE, []
        scale = data.get("scale") if isinstance(data, dict) else None
        if isinstance(scale, bool) or not isinstance(scale, (int, float)) or not math.isfinite(scale) or scale <= 0:
            return STEP_SCALE, []
        history = data.get("history")
        kept = [h for h in history if isinstance(h, dict)] if isinstance(history, list) else []
        return float(scale), kept

    def _local_trials(self, children: Sequence[Candidate]) -> list[tuple[Candidate, Candidate]]:
        """The round's local steps the 1/5 rule counts, each with its parent:
        evaluated children with `operator == "param_local"` (the fallbacks of
        `param_cross` and `param_tpe` included) whose parent has a fitness. A
        child refused before evaluation says nothing about the step's size."""
        trials = []
        for child in children:
            if child.operator != "param_local" or child.rejected:
                continue
            parent = self.by_id.get(child.parent_id or "")
            if parent is not None and parent.fitness is not None:
                trials.append((child, parent))
        return trials

    def _adapt_step(self, generation: int, children: Sequence[Candidate]) -> None:
        """The 1/5 rule per trial (Decision 16), once per recorded round: of the
        round's local steps (`_local_trials`), `improved` are those that compete
        with a fitness above their parent's by more than the elite rule's noise
        margin (`grid.margin`; 0 with no noise estimate), and the step is
        multiplied by `1.2 ** (improved - 0.2 * tried)`, within `STEP_LIMITS`.
        That balances at a success rate of exactly 20 % however many local
        children a round has (a threshold on each round's share would settle at
        50 % with one child, 29 % with two). A round without local steps leaves
        it; a round already in the history is not counted again."""
        if any(h.get("generation") == generation for h in self._step_history):
            return
        trials = self._local_trials(children)
        if not trials:
            return
        margin = self.grid.margin
        improved = sum(
            1 for child, parent in trials
            if _competes(child) and child.fitness > parent.fitness + margin  # type: ignore[operator]
        )
        low, high = self.STEP_LIMITS
        grown = self.step_scale * self.STEP_FACTOR ** (improved - self.STEP_SUCCESS * len(trials))
        self.step_scale = min(high, max(low, grown))
        self._step_history.append(
            {"generation": generation, "improved": improved, "tried": len(trials), "scale": self.step_scale}
        )
        path = self._perturb_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(path, json.dumps({"scale": self.step_scale, "history": self._step_history}, indent=1) + "\n")

    def _replay_missing_step(self) -> None:
        """On resume: the latest recorded round with local steps, when
        `perturb.json`'s history lacks it (the run died between writing the
        round's rows and the file), is counted now, with the noise as it stands.
        A run written before the file (several such rounds and no history)
        keeps the default size: only a missing *last* update is replayed."""
        rounds: dict[int, list[Candidate]] = {}
        for candidate in self.archive:
            if int(candidate.generation) >= 1:
                rounds.setdefault(int(candidate.generation), []).append(candidate)
        stepped = [g for g in sorted(rounds) if self._local_trials(rounds[g])]
        if not stepped:
            return
        recorded = {h.get("generation") for h in self._step_history}
        if stepped[-1] in recorded:
            return
        if not self._step_history and len(stepped) > 1:
            return
        self._adapt_step(stepped[-1], rounds[stepped[-1]])

    # -- one Latin hypercube for the run (`param_lhs`) ----------------------

    LHS_ROWS = (4, 64)
    """The fewest and the most rows of one cube."""

    def _lhs_path(self) -> Path:
        return self.ledger.run_dir / "work" / "lhs.json"

    def _read_lhs(self) -> dict[str, dict]:
        """`work/lhs.json`'s cubes; none for a run written before it (or before
        its first `param_lhs` child), and a malformed entry is dropped: a cube
        is then built at the next `param_lhs` child."""
        try:
            data = json.loads(self._lhs_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        cubes = data.get("cubes") if isinstance(data, dict) else None
        if not isinstance(cubes, dict):
            return {}
        kept: dict[str, dict] = {}
        for key, cube in cubes.items():
            if (
                isinstance(cube, dict)
                and isinstance(cube.get("rows"), list)
                and all(isinstance(row, dict) for row in cube["rows"])
                and isinstance(cube.get("next"), int)
                and isinstance(cube.get("K"), int)
                and isinstance(cube.get("built"), int)
            ):
                kept[str(key)] = cube
        return kept

    def _write_lhs(self) -> None:
        path = self._lhs_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(path, json.dumps({"cubes": self._lhs}, indent=1) + "\n")

    def _lhs_size(self) -> int:
        """The run's expected number of `param_lhs` children, clamped to
        `LHS_ROWS`: `generations x children x share(param_lhs) / sum of shares`."""
        search = self.config.search
        total = sum(w for w in search.operators.values() if w > 0)
        share = max(0.0, search.operators.get("param_lhs", 0.0))
        expected = search.generations * search.children_per_generation * share / total if total > 0 else 0.0
        low, high = self.LHS_ROWS
        return min(high, max(low, math.ceil(expected - 1e-9)))

    def _lhs_take(self, parent: Candidate) -> tuple[dict, list[str]] | None:
        """The next row of the run's hypercube for `parent`'s space, and the
        names of its parameters drawn near a prior. `None` when an untyped
        parent declares nothing to sweep.

        A typed space has one cube (`"typed"`); a `# PARAMS` block one per
        declaration, keyed by its ranges as sorted JSON. A row that breaks a
        constraint, or equals the parent's configuration, is passed over; after
        a whole cube's worth of such rows the last is handed out anyway (the
        static stage has the last word). When the rows run out a new cube is
        built from one draw of the run's generator -- the only number a
        `param_lhs` child ever draws from it.

        A cube is kept until it is used up when only the run's size changes
        (generations, children, shares); a typed cube records its space
        (`"space"`: names, types, ranges, scales, choices, priors) and is built
        anew when a resumed run declares the space differently."""
        space = self.config.problem.parameters
        signature: str | None = None
        if space is not None:
            key, base, ok = "typed", dict(parent.params or space.defaults()), space.satisfies
            names = set(space.names)
            signature = _space_signature(space)

            def build(size: int, rng: random.Random) -> tuple[list[dict], list[list[str]]]:
                drawn = latin_rows(space, size, rng)
                return [values for values, _ in drawn], [prior for _, prior in drawn]
        else:
            ranges = declared_ranges(parent.block)
            base = current_values(parent.block, set(ranges)) if ranges else {}
            if not base:
                return None
            key, ok, names = "params:" + json.dumps(ranges, sort_keys=True), (lambda _row: True), set(base)

            def build(size: int, rng: random.Random) -> tuple[list[dict], list[list[str]]]:
                rows = rows_for_params(ranges, base, latin_hypercube(ranges, size, rng.randrange(1 << 30)))
                return rows, [[] for _ in rows]

        passed = 0
        while True:
            cube = self._lhs.get(key)
            if cube is not None and (
                (cube["rows"] and set(cube["rows"][0]) != names)
                or (signature is not None and cube.get("space") != signature)
            ):
                cube = None  # a cube of another space: not this one's rows
            if cube is None or cube["next"] >= len(cube["rows"]):
                size = self._lhs_size()
                rows, priors = build(size, random.Random(self.rng.randrange(1 << 30)))
                cube = {"K": size, "built": (cube or {}).get("built", 0) + 1, "next": 0, "rows": rows}
                if signature is not None:
                    cube["space"] = signature
                if any(priors):
                    cube["prior"] = priors
                self._lhs[key] = cube
            index = cube["next"]
            cube["next"] = index + 1
            row = dict(cube["rows"][index])
            prior = list(cube.get("prior", [[]] * len(cube["rows"]))[index])
            passed += 1
            if (row != base and ok(row)) or passed >= cube["K"]:
                break
        self._write_lhs()
        return row, prior

    def _next_lhs_row(self, parent: Candidate) -> dict | None:
        """The row the next `param_lhs` child of `parent` takes (`_lhs_take`)."""
        taken = self._lhs_take(parent)
        return taken[0] if taken is not None else None

    def _observations(self) -> list[Observation]:
        """Every configuration that was evaluated, as a rank score (higher is
        better, minus the position): only the order reaches the estimator, so
        numbers on different scales never meet. Four tiers, best first:

        1. finished (it competes), by fitness;
        2. screened out by a cheaper stage, by the deepest stage it reached and
           that stage's score -- whatever the scale, since it is ranked below
           every finished one;
        3. raced out, or kept out by the hold-out veto: one shared rank. The
           race has just shown it to be behind the best, whatever its
           screening score; a vetoed one does worse than the seed where it was
           not selected on, and the estimator must not learn to propose there;
        4. failed or gated: one shared rank, the worst -- a region not to
           propose in again.

        Ties (inside a tier, or a whole tier 3 or 4) share the mean position.

        A candidate that reached the final stage without finishing it, and
        without failing, being raced out, vetoed or gated (abandoned: a stop
        request, the time limit, the daily cap), is no observation at all: its
        stage was cut short for a reason that says nothing about it, and as a
        failure it would steer the estimator away from a region on no evidence."""
        index = {stage.id: i for i, stage in enumerate(self.config.evaluate.stages)}
        final = self.config.final_stage.id
        finished: list[tuple[float, Candidate]] = []
        screened: list[tuple[tuple[int, float], Candidate]] = []
        behind: list[Candidate] = []
        failed: list[Candidate] = []
        for c in self.archive:
            if not c.params or c.rejected:
                continue
            if c.last_failure or c.gated:
                failed.append(c)
            elif c.raced_out or c.veto:
                behind.append(c)
            elif c.competes and c.fitness is not None:
                finished.append((float(c.fitness), c))
            elif c.stages_reached and final not in c.stages_reached:
                deepest = c.stages_reached[-1]
                score = c.stage_scores.get(deepest, c.score)
                if score is not None:
                    screened.append(((index.get(deepest, -1), float(score)), c))
        groups: list[list[Candidate]] = []
        for tier in (finished, screened):
            tier.sort(key=lambda pair: pair[0], reverse=True)
            for i, (key, c) in enumerate(tier):
                if i and key == tier[i - 1][0]:
                    groups[-1].append(c)
                else:
                    groups.append([c])
        groups += [tier for tier in (behind, failed) if tier]
        observations: list[Observation] = []
        position = 1
        for group in groups:
            rank = position + (len(group) - 1) / 2
            observations += [Observation(c.params, -rank) for c in group]
            position += len(group)
        return observations

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

    def _parent_block(self, candidate: Candidate) -> str | None:
        """The block `candidate` was bred from; `None` for the starting point
        or a parent this run does not hold (the near gate then indexes the
        candidate's whole block)."""
        parent = self.by_id.get(candidate.parent_id or "")
        return parent.block if parent is not None else None

    def _summary_of(self, candidate: Candidate) -> str:
        parent = self.by_id.get(candidate.parent_id or "")
        return change_summary(
            candidate.block,
            parent.block if parent else None,
            candidate.parent_id,
            params=candidate.params,
            parent_params=parent.params if parent else None,
            tree=self.repo is not None,
        )

    def _parent_ledger(self, parent: Candidate) -> tuple[LedgerEntry, ...]:
        """What was tried on `parent`: its last children, newest first, read from
        the archive alone (so a resumed run shows the same) and drawing nothing
        from the run's random stream."""
        best = self.best
        return parent_ledger(
            self.archive, parent, by_id=self.by_id, best_id=best.id if best else None,
            final_stage=self.config.final_stage.id, tree=self.repo is not None,
        )

    def _inspirations(self, parent: Candidate, operator: str) -> list[Inspiration]:
        """1 to `search.inspirations` elites from other cells; only crossover is shown their code.

        An operator that calls no model (role "none", the `param_*` ones) never
        reads them, so none are drawn: on a typed space the parameter axes
        spread the archive over many cells, and drawing from them would shift
        every later number of the run (A2 decision 19). The count is still
        drawn, so the stream is the one a single-cell grid gives."""
        budget = self.config.search.inspirations
        if budget <= 0:
            return []
        wanted = 1 if operator == "crossover" else self.rng.randint(1, budget)
        if _role_of(operator) == "none":
            return []
        picks = self.grid.sample_inspirations(parent, wanted, self.rng)
        return [
            Inspiration(
                id=c.id,
                summary=self._summary_of(c),
                score_line=self._score_line(c),
                # On a tree an inspiration is shown as its change (a diff), for every operator.
                block=c.block if operator == "crossover" or self.repo is not None else None,
            )
            for c in picks
        ]

    # -- dead ends: behavioural twins, shown to every parent -------------

    DEAD_END_LIMIT = 6

    def _remember_dead_end(self, candidate: Candidate) -> None:
        """Keep a one-line fingerprint of a behavioural twin for every later prompt.

        The parent's ledger (`_parent_ledger`) only reaches the parent that bred the twin.
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
        self._taken = []  # this generation's proposals so far (`_breed_one`)
        for slot, operator in enumerate(plan):
            if not self.budget.check().allowed or self._provider_is_dead() or self.run_stop.triggered:
                break
            if _role_of(operator) != "none" and not self._ai_room():
                break  # what is left could not pay for this call: the round ends here
            # A big step is an escape from the *front* of the search, so it
            # starts from the best candidate rather than from a sampled cell.
            parent = (best or pool[0]) if operator == "big_step" else pool[slot % len(pool)]
            if operator in TYPED_SPACE_OPERATORS:
                parent = self._tuning_parent(parent)
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
        role = _role_of(operator)

        outcome, verdict, attempts = self._propose(
            operator, parent, inspirations, candidate_id, generation, role
        )
        if outcome.ok and isinstance(outcome.meta.get("params"), dict):
            # Q19: any sibling with a configuration, whichever operator proposed it.
            self._taken.append(dict(outcome.meta["params"]))
        if operator == "big_step" and outcome.ok:
            # The response parsed and applied; `_restore_stop_policy` reads the same from the row.
            self._count_big_step(
                generation, tried=False, applied=True, plateau=self._big_step_on_plateau
            )

        child = Candidate(
            id=candidate_id,
            generation=generation,
            block=outcome.block if outcome.ok else parent.block,
            source=self._make_source(outcome.block if outcome.ok else parent.block, candidate_id),
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
        # A failed agent call is billed (it carries a completion) and still counts as a failure; a reply stopped at its cap does not.
        if outcome.completion is not None and not outcome.provider_error:
            self._provider_failures = 0
        elif outcome.provider_error and not _stopped_at_cap(outcome):
            self._provider_failures += 1
            self._provider_halt = outcome.error or "provider error"
        if not outcome.ok:
            child.rejected = True
            child.competes = False
            child.reject_reason = outcome.error
            child.last_failure = outcome.error
            child.score = self.config.evaluate.failure_score
        elif verdict is not None and verdict.rejects:
            child.rejected = True
            child.competes = False
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
        self.events.emit(
            "candidate_bred",
            candidate_id=child.id,
            generation=generation,
            operator=child.operator,
            parent_id=parent.id,
            attempts=attempts,
            ok=not child.rejected,
            novelty=child.novelty,
            reason=child.reject_reason,
        )
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
        reason = self._big_step_reason if operator == "big_step" else None
        if reason:
            hint = "\n\n".join(part for part in (reason, hint) if part)
        # What was tried on this parent, for the operators that call a model through
        # `run_operator`; the others never read it, so it is not built for them.
        ledger = (
            self._parent_ledger(parent)
            if role != "none" and operator != "agent_patch"
            else ()
        )
        outcome = self._invoke(operator, parent, inspirations, role, hint, candidate_id, ledger)
        attempts += 1
        self._bill(outcome, candidate_id, generation, operator, role)
        self._trace(outcome, candidate_id, parent, operator, role, attempt=attempts)
        if not outcome.ok:
            return outcome, None, attempts

        wants_near = role != "none"
        verdict = self.novelty.check(
            outcome.block or "", parent.block, parent.id, near=wants_near
        )
        if verdict.novel or not self.config.search.novelty_retry:
            return outcome, verdict, attempts
        if role == "none" and operator != "param_lhs":
            # Seeded from the run's stream once: asked again it would hand back the same.
            # A `param_lhs` child asked again takes the cube's next row, and costs nothing.
            return outcome, verdict, attempts
        if operator == "agent_patch":
            # A whole agent run is not re-asked for a duplicate: the next round's brief shows it.
            return outcome, verdict, attempts
        if role != "none" and not self._ai_room():
            return outcome, verdict, attempts

        # One re-prompt, ever. The first real run showed the small model
        # handing back the same program three times; asking twice is worth the
        # cheap call, asking a third time is how v1 built its token bill.
        # A big step's re-prompt still says why it is due, as its first attempt did.
        retry_hint = "\n\n".join(part for part in (reason, verdict.retry_hint) if part)
        retry = self._invoke(operator, parent, inspirations, role, retry_hint, candidate_id, ledger)
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
        candidate_id: str,
        ledger: Sequence[LedgerEntry] = (),
    ) -> OperatorResult:
        if operator == "param_lhs":
            taken = self._lhs_take(parent)
            space = self.config.problem.parameters
            if taken is None:  # the parent declares nothing to sweep: the operator says so
                return param_lhs(parent, row={})
            row, prior = taken
            if space is not None:
                own = dict(parent.params or space.defaults())
            else:
                own = current_values(parent.block, set(declared_ranges(parent.block)))
            if row == own:  # a whole cube's worth of rows equal the parent: a space of one point
                return OperatorResult(operator="param_lhs", messages=[],
                                      error="param_lhs found nothing to sweep: every sample equals the parent")
            if space is not None:
                return param_lhs_typed(space, parent, row=row, prior=prior)
            return param_lhs(parent, row=row)
        if operator in TYPED_SPACE_OPERATORS:
            seed = self.rng.randrange(1 << 30)
            space = self.config.problem.parameters
            assert space is not None  # `config._check_typed_operators`
            scale = self.step_scale
            if operator == "param_local":
                return param_local(space, parent, seed=seed, scale=scale)
            if operator == "param_cross":
                return param_cross(space, parent, self._mate_for(parent), seed=seed, scale=scale)
            return param_tpe(space, parent, self._observations(), seed=seed, taken=self._taken, scale=scale)
        if operator == "agent_patch":
            return self._agent_patch(parent, role, candidate_id)
        return run_operator(
            operator,
            config=self.config,
            provider=self._calling(role),
            model_role=role,
            parent=parent,
            parent_result=self.results.get(parent.id),
            skeleton_prefix=self.prefix,
            skeleton_suffix=self.suffix,
            best_score=self.best.fitness if self.best else None,
            inspirations=inspirations,
            scratchpad=self.scratchpad.text or None,
            extra_instruction=hint,
            tree=self._tree_context(candidate_id),
            ledger=ledger,
        )

    AGENT_FAILURES = 3
    """The most recent failures an agent's brief shows."""

    def _agent_patch(self, parent: Candidate, role: str, candidate_id: str) -> OperatorResult:
        """A coding agent on the parent's tree (`search/agent_patch.py`): shown the
        archive's best and worst and the last failures, bounded by the study's time
        left and what is left of `budget.max_usd`, ended by a Stop request."""
        from evolvekit.search.agent_patch import agent_patch

        context = self._tree_context(candidate_id)
        if context is None:  # `config._tree_rules` refuses agent_patch without a tree
            return OperatorResult(operator="agent_patch", messages=[],
                                  error="agent_patch changes a source tree, and this problem has none")
        best = self.best
        competing = [c for c in self.archive if _competes(c) and c.fitness is not None]
        worst = min(competing, key=lambda c: (c.fitness, c.id)) if competing else None
        left_usd = self.budget.config.max_usd - self.budget.state.usd
        provider = self._calling(role)
        note_for = getattr(provider, "act_cap_note", None)
        note = note_for() if callable(note_for) else None
        if note and note not in self._told:  # once per run, not per call
            self._told.add(note)
            self.log(f"warning: {note}")
        return agent_patch(
            config=self.config,
            provider=provider,
            model_role=role,
            parent=parent,
            parent_result=self.results.get(parent.id),
            context=context,
            best=best,
            worst=None if worst is None or (best is not None and worst.id == best.id) else worst,
            failures=self._failures(),
            time_left_s=self.run_stop.left_s(),
            left_usd=max(0.0, left_usd),
            cancel=self.run_stop.event,
        )

    def _failures(self) -> list[str]:
        """The archive's last `AGENT_FAILURES` failures, oldest first, each the
        candidate's id, its reason and its feedback's tail (`FAILURE_LIMIT` at most)."""
        from evolvekit.prompts import FAILURE_LIMIT

        found: list[str] = []
        for candidate in reversed(self.archive):
            failure = (candidate.last_failure or "").strip()
            if not failure:
                continue
            head, _, rest = failure.partition("\n")
            if head.startswith("stage "):  # the stage's own feedback is the fuller tail
                stage = head[len("stage "):].split(":", 1)[0]
                rest = (candidate.feedback or {}).get(stage) or rest
            lead = f"{candidate.id}: {head}"
            room = max(0, FAILURE_LIMIT - len(lead) - 1)
            found.append(f"{lead}\n{rest[-room:]}" if rest.strip() and room else lead[:FAILURE_LIMIT])
            if len(found) == self.AGENT_FAILURES:
                break
        return list(reversed(found))

    def _tree_context(self, candidate_id: str) -> TreeContext | None:
        """For a tree problem: where the operator checks the parent out (a draft
        beside the candidate's own tree), and the run's last refused changes."""
        if self.repo is None:
            return None
        problem = self.config.problem.tree
        assert problem is not None
        return TreeContext(self.ledger.run_dir, self.repo, problem.base, tuple(problem.open),
                           f"{candidate_id}.draft", refusals=self._refusals())

    def _refusals(self) -> tuple:
        """The most recent candidates the build or the static check refused, oldest
        first: a refused candidate is never a parent, so without this its build's
        stderr would never reach the model."""
        from evolvekit.tree_prompts import REFUSALS_SHOWN, Refusal

        hard = {s.id for s in self.config.evaluate.stages if s.kind in ("builtin-static", "builtin-build")}
        found: list[Refusal] = []
        for candidate in reversed(self.archive):
            failure = candidate.last_failure or ""
            if not candidate.rejected or not failure.startswith("stage "):
                continue
            head, _, rest = failure.partition("\n")
            stage = head[len("stage "):].split(":", 1)[0]
            if stage not in hard:
                continue
            tail = (candidate.feedback or {}).get(stage) or rest
            found.append(Refusal(candidate.id, candidate.reject_reason or head, tail))
            if len(found) == REFUSALS_SHOWN:
                break
        return tuple(reversed(found))

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
        if usage.priced_from == "cap":
            self.log(f"        {candidate_id} the agent's cost was not reported; charged its cap ${usage.usd:.2f}")
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
                **({"dropped": outcome.meta["dropped"]} if "dropped" in outcome.meta else {}),
            },
        )

    # -- the meta-scratchpad ---------------------------------------------

    def _maybe_refresh_scratchpad(self, generation: int) -> None:
        if not self.config.search.uses_llm:
            return  # the scratchpad is written by the small model
        if not self.scratchpad.due(generation) or not self._ai_room():
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
            provider=self._calling("small"),
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
    ) -> bool:
        """Evaluate a generation and record it. True when the run was stopped
        in the middle of it (`evolvekit/stopping.py`): then nothing is
        recorded -- a generation is recorded whole or not at all, and the
        resumed run evaluates it again, its finished runs from the cache."""
        if not candidates:
            return False
        tree_problem = self.config.problem.tree
        if tree_problem is not None and tree_problem.build:
            # A build installs one tree where the commands find it, so two
            # candidates cannot be evaluated at once: one after the other, in
            # order (a stage's `workers` still run one candidate's cases together).
            prior, own = self._archive_scores(), self._archive_stage_scores()
            results: dict[str, EvalResult] = {}
            for candidate in candidates:
                results.update(self.cascade.evaluate_generation([candidate], prior, stage_scores=own))
                self._drop_trees([candidate])
                if any(result.abandoned for result in results.values()):
                    return True
        else:
            results = self.cascade.evaluate_generation(
                candidates, self._archive_scores(), stage_scores=self._archive_stage_scores()
            )
            self._drop_trees(candidates)
        if any(result.abandoned for result in results.values()):
            return True
        # The generation's outcomes have joined the estimate: the elite rule
        # below and the stop policy after the round read it as it is now.
        self._refresh_noise()
        final = self.config.final_stage
        for candidate in candidates:
            result = results[candidate.id]
            self.results[candidate.id] = result
            candidate.replicates = final.seeds if final.id in result.stages_reached else 0
            candidate.commit = result.commit
            candidate.score = result.score
            candidate.params = result.params
            candidate.competes = result.competes
            candidate.raced_out = result.raced_out
            candidate.gated = result.gated
            candidate.veto = result.veto
            candidate.holdout_margin = result.holdout_margin
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
            candidate.behaviour_values = result.behaviour_values
            candidate.behaviour_twin_id = result.behaviour_twin_id
            if result.behaviour_twin_id is not None:
                candidate.novelty = "behavioural"
                self._note_behavioural_twin(candidate)
            # Placement first: the cell coordinate belongs in the ledger row.
            self.grid.add(candidate)
            if not candidate.rejected:
                self.novelty.add(candidate.id, candidate.block, parent_block=self._parent_block(candidate))
            self._record(candidate)
        self._check_screens(generation)
        return False

    # -- screening (search/screening.py) ----------------------------------

    def _screen_controls(self) -> dict[str, ScreenControl]:
        path = self.ledger.run_dir / "work" / SCREENING_FILE
        return {
            stage.id: ScreenControl.load(
                path, stage.id, int(stage.promote.top_k_per_generation or 0), self.children_per_generation,
                later=later.id,
            )
            for stage, later in adapting_stages(self.config)
        }

    def _apply_screens(self) -> None:
        for stage_id, control in self.screens.items():
            self.cascade.promote_k[stage_id] = control.k
            if control.off:
                self.cascade.skipped.add(stage_id)

    def _check_screens(self, generation: int, only: Sequence[str] | None = None) -> None:
        """After a recorded round: each adapting stage's check over every
        candidate scored at it and at the next stage (the seed included, as
        `status`'s screening agreement counts them), cumulative over the run.
        `only`: these stages' checks alone (`_replay_missing_screen_checks`)."""
        for stage_id, control in self.screens.items():
            if control.off or (only is not None and stage_id not in only):
                continue
            pairs = screen_pairs(self.archive, stage_id, control.later)
            control.cap = self.children_per_generation
            said = control.after_round(generation, pairs)
            self._apply_screens()
            if said is None:
                continue
            self.log(f"        {said}")
            last = control.history[-1]
            self.events.emit("screening_changed", stage=stage_id, k=control.k, off=control.off,
                             rho=last["rho"], n=last["n"])

    def _replay_missing_screen_checks(self, last_generation: int) -> None:
        """On resume: the latest recorded round's screening check, for each
        adapting stage whose `screening.json` history lacks it (the run died
        between writing the round's rows and the file), made now over the same
        candidates -- the check reads the archive only, so it says what the
        lost one said. A check that read no ρ (too few pairs) left no history
        entry and is made again: it reads none again and changes nothing."""
        missing = [
            stage_id for stage_id, control in self.screens.items()
            if not control.off and all(h.get("generation") != last_generation for h in control.history)
        ]
        if missing:
            self._check_screens(last_generation, only=missing)

    def _drop_trees(self, candidates: Sequence[Candidate]) -> None:
        """Each candidate's tree, removed once its evaluation has finished: no
        reader needs it after that (an operator checks the parent out as a
        draft, the final check makes its own trees, and `study export --diff`
        and `study pr` rebuild the change from the recorded block)."""
        if self.repo is None:
            return
        from evolvekit import tree

        for candidate in candidates:
            self._remove_tree(tree, candidate.id)

    def _remove_tree(self, tree, candidate_id: str) -> bool:
        if tree.remove(self.ledger.run_dir, candidate_id):
            return True
        self.log(f"a combination's folder is still in use and stays: {tree.tree_dir(self.ledger.run_dir, candidate_id)}")
        return False

    def _note_behavioural_twin(self, candidate: Candidate) -> None:
        """Log the twin and keep it among the run's dead ends (`_compose_hint`).
        The parent's next prompt learns of it from its ledger (`_parent_ledger`),
        which a resumed run rebuilds from `runs.jsonl`.

        The wording is deliberately problem-agnostic: the loop knows only that
        every number the evaluator reported came back identical, which means
        the child made the same decisions as an earlier program however
        differently it was written. What "decision" means is the problem
        description's business, not the framework's.
        """
        self.log(f"        {candidate.id}: {candidate.reject_reason}")
        self._remember_dead_end(candidate)

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


def adapting_stages(config: Config) -> list[tuple]:
    """`(stage, next stage)` for every command stage with `promote.adapt` and a
    top k whose next stage is a command stage: what gets a screening controller.
    None with `problem.tree.build`: candidates are then evaluated one at a
    time, so a round's top k is of one."""
    tree = config.problem.tree
    if tree is not None and tree.build:
        return []
    stages = config.evaluate.stages
    return [
        (stage, later)
        for stage, later in zip(stages, stages[1:])
        if stage.promote.adapt and stage.promote.top_k_per_generation is not None
        and stage.kind == "command" and later.kind == "command"
    ]


def screen_pairs(candidates: Sequence[Candidate], stage_id: str, later: str) -> list[tuple[float, float]]:
    """`(score at stage_id, score at later)` over every candidate scored at
    `stage_id`, not rejected and not failed. One raced out at `later` was
    judged behind there: it ranks last at `later` (one value below every
    finished score, ties among them), rather than leaving the pairs -- else a
    race would hide exactly the screen's mistakes."""
    finished: list[tuple[float, float]] = []
    behind: list[float] = []
    for c in candidates:
        if c.rejected or c.last_failure or stage_id not in c.stage_scores:
            continue
        if later in c.stage_scores:
            finished.append((float(c.stage_scores[stage_id]), float(c.stage_scores[later])))
        elif c.raced_out:
            behind.append(float(c.stage_scores[stage_id]))
    if not behind:
        return finished
    floor = min((b for _, b in finished), default=0.0) - 1.0
    return finished + [(a, floor) for a in behind]


def _space_signature(space) -> str:
    """What a typed cube's rows depend on: each parameter's name, type, range,
    scale, choices and prior (not its default, help or the constraints, which
    are read when a row is handed out)."""
    return json.dumps(
        [{**{k: v for k, v in p.describe().items() if k not in ("default", "help")}, "prior": p.prior}
         for p in space.parameters],
        sort_keys=True, default=str,
    )


def _role_of(operator: str) -> str:
    """The model role an operator calls ("none": it calls no model)."""
    return "strong" if operator == "big_step" else OPERATOR_ROLES.get(operator, "small")


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.6g}"


def _generation_durations(events: Sequence[dict]) -> dict[int, float]:
    """`generation -> duration_s` from every `generation_finished` event; the
    last one counts when a generation was finished twice."""
    durations: dict[int, float] = {}
    for event in events:
        value = event.get("duration_s")
        if event.get("type") == "generation_finished" and isinstance(event.get("generation"), int) \
                and isinstance(value, (int, float)) and not isinstance(value, bool):
            durations[event["generation"]] = float(value)
    return durations


def _generation_plateaus(events: Sequence[dict]) -> dict[int, bool]:
    """`generation -> plateau` from every `generation_started` event that
    records it (the last one counts when a generation was started twice);
    events from before it was recorded give nothing."""
    plateaus: dict[int, bool] = {}
    for event in events:
        if event.get("type") == "generation_started" and isinstance(event.get("generation"), int) \
                and isinstance(event.get("plateau"), bool):
            plateaus[event["generation"]] = event["plateau"]
    return plateaus


def _seed_stage_durations(events: Sequence[dict]) -> dict[str, float]:
    """`stage -> duration_s` of the seed (generation 0) from the `stage_finished`
    events between its `generation_started` and the next generation's; the last
    one counts when the seed was evaluated twice. The final check's private
    stages are not the search's."""
    durations: dict[str, float] = {}
    generation = None
    for event in events:
        kind = event.get("type")
        if kind == "generation_started":
            generation = event.get("generation")
        elif kind == "stage_finished" and generation == 0 and not event.get("private"):
            value = event.get("duration_s")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                durations[str(event.get("stage"))] = float(value)
    return durations


def _comparable(problem: dict) -> dict:
    """A problem identity with each stage command as its tokens: a doubled
    space or a trailing blank changes the string, not the command."""
    stages = [
        {**stage, "command": _command_tokens(stage.get("command"))} if isinstance(stage, dict) else stage
        for stage in problem.get("stages") or []
    ]
    return {**problem, "stages": stages}


def _command_tokens(command: object) -> list[str] | None:
    if not isinstance(command, str):
        return None
    try:
        # Not POSIX rules: a Windows command's backslashes are part of it.
        return shlex.split(command, posix=False)
    except ValueError:  # an unbalanced quote: the command is still its words
        return command.split()


def _competes(candidate: Candidate) -> bool:
    return (
        not candidate.rejected
        and candidate.competes
        and candidate.fitness is not None
    )


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


def _stopped_at_cap(outcome: OperatorResult) -> bool:
    """A reply the backend stopped at its spending cap: billed, but the backend is alive."""
    raw = outcome.completion.raw if outcome.completion is not None else None
    return bool(raw) and raw.get("stopped_at") == "error_max_budget_usd"
