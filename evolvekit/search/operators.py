"""Mutation operators: `diff`, `rewrite`, `crossover`, `big_step`, `param_lhs`.

Each LLM operator is one call and one attempt at turning the response into a
new block. There is no retry ladder: a response that cannot be applied becomes
a failed child, its error is logged, and the next generation moves on. v1's
nested 3x3 retry was the single largest multiplier on its token bill. The one
exception Phase B adds is the novelty re-prompt, which the driver owns: it is
bounded to one extra call and only fires when the model handed back something
it had already been paid for.

`param_lhs` is the odd one out -- no provider, no tokens, no prompt. It sweeps
the `# PARAMS:` ranges the block declares (see `search/params.py`).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from evolvekit.candidate import SEED_OPERATOR, Candidate, splice_block
from evolvekit.config import Config
from evolvekit.diffs import DiffError, apply_response
from evolvekit.evaluate.types import EvalResult
from evolvekit.prompts import Inspiration, build_messages
from evolvekit.providers.base import Completion, Provider, ProviderError
from evolvekit.search.params import apply_params, current_values, declared_ranges
from evolvekit.search.tuning import Observation, propose_tpe
from evolvekit.space import PRIOR_BAND, STEP_SCALE, Parameter, ParameterSpace

if TYPE_CHECKING:  # `evolvekit.tree` is imported only when a tree problem is handled
    from evolvekit.tree import Repo
    from evolvekit.tree_prompts import Refusal

__all__ = [
    "OperatorResult",
    "TREE_OPERATORS",
    "TreeContext",
    "run_operator",
    "param_lhs",
    "param_lhs_typed",
    "latin_rows",
    "param_local",
    "param_cross",
    "param_tpe",
    "OPERATOR_ROLES",
]

OPERATOR_ROLES = {
    "diff": "small",
    "rewrite": "small",
    "crossover": "small",
    "param_lhs": "none",
    "param_local": "none",
    "param_cross": "none",
    "param_tpe": "none",
    "big_step": "strong",
    "agent_patch": "strong",  # a coding agent on the parent's tree (`search/agent_patch.py`)
}


@dataclass
class OperatorResult:
    """What one operator invocation produced -- success or failure, never both."""

    operator: str
    messages: list[dict[str, str]]
    completion: Completion | None = None
    block: str | None = None
    mode: str | None = None
    error: str | None = None
    provider_error: bool = False  # the backend failed, not the model's answer
    meta: dict[str, object] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.block is not None


TREE_OPERATORS = ("diff",)
"""The LLM operators that work on a tree problem (decision 37): `rewrite`,
`crossover` and `big_step` are not compiled for trees, and refused here."""


@dataclass(frozen=True)
class TreeContext:
    """What an operator on a tree problem needs beyond a skeleton's: where the
    parent's tree is checked out (`<run>/work/trees/<draft_id>`, removed again),
    its base, the open files' globs, and the run's last refused changes."""

    run_dir: Path
    repo: "Repo"
    base: str
    open: tuple[str, ...]
    draft_id: str
    refusals: "tuple[Refusal, ...]" = ()


def run_operator(
    operator: str,
    *,
    config: Config,
    provider: Provider,
    model_role: str,
    parent: Candidate,
    parent_result: EvalResult | None,
    skeleton_prefix: str,
    skeleton_suffix: str,
    best_score: float | None = None,
    inspirations: "list[Inspiration] | tuple[Inspiration, ...]" = (),
    scratchpad: str | None = None,
    extra_instruction: str | None = None,
    tree: TreeContext | None = None,
) -> OperatorResult:
    """One LLM call, one apply attempt. With `tree`, on the parent's tree (`_on_tree`)."""
    if tree is not None:
        return _on_tree(operator, config=config, provider=provider, model_role=model_role, parent=parent,
                        parent_result=parent_result, best_score=best_score, inspirations=inspirations,
                        scratchpad=scratchpad, extra_instruction=extra_instruction, ctx=tree)
    model_cfg = config.models.by_role(model_role)
    messages = build_messages(
        config,
        skeleton_prefix=skeleton_prefix,
        skeleton_suffix=skeleton_suffix,
        parent=parent,
        result=parent_result,
        operator=operator,
        best_score=best_score,
        inspirations=inspirations,
        scratchpad=scratchpad,
        extra_instruction=extra_instruction,
    )
    try:
        completion = provider.complete(
            messages,
            model=model_cfg.model,
            max_tokens=model_cfg.max_tokens,
            temperature=model_cfg.temperature,
        )
    except ProviderError as exc:  # billed what it cost, when it ran and said so
        return OperatorResult(
            operator=operator, messages=messages, error=str(exc), provider_error=True, completion=exc.completion
        )

    try:
        block, mode = apply_response(parent.block, completion.text)
    except DiffError as exc:
        return OperatorResult(
            operator=operator,
            messages=messages,
            completion=completion,
            error=f"could not apply response: {exc}",
        )
    return OperatorResult(
        operator=operator,
        messages=messages,
        completion=completion,
        block=block,
        mode=mode,
    )


def _on_tree(
    operator: str,
    *,
    config: Config,
    provider: Provider,
    model_role: str,
    parent: Candidate,
    parent_result: EvalResult | None,
    best_score: float | None,
    inspirations: "list[Inspiration] | tuple[Inspiration, ...]",
    scratchpad: str | None,
    extra_instruction: str | None,
    ctx: TreeContext,
) -> OperatorResult:
    """The parent's tree checked out as a draft; the open files (or their one
    marked block) shown; the reply applied to the draft; the child is the
    draft's diff against the base (cumulative). A reply that does not apply,
    changes nothing, or changes a file outside `open` is a format failure."""
    if operator not in TREE_OPERATORS:
        return OperatorResult(
            operator=operator, messages=[],
            error=(f"the {operator} operator does not work on a source tree; "
                   f"a code study uses {', '.join(TREE_OPERATORS)}"),
        )
    from evolvekit import tree as trees
    from evolvekit.tree_prompts import (
        OPEN_LINES,
        for_model,
        marked_block,
        open_files,
        open_lines_problem,
        parse_diff_response,
        shown_lines,
        tree_messages,
    )

    problem = config.problem
    model_cfg = config.models.by_role(model_role)
    messages: list[dict[str, str]] = []
    completion: Completion | None = None

    def failed(error: str) -> OperatorResult:
        return OperatorResult(operator=operator, messages=messages, completion=completion, error=error)

    try:
        draft = trees.materialise(ctx.run_dir, repo=ctx.repo, base=ctx.base, diff=parent.block,
                                  candidate_id=ctx.draft_id)
    except trees.TreeError as exc:
        # Not the model's answer: git or the run folder failed. Unpaid, and counted
        # like a backend failure, so a run whose trees cannot be made stops.
        return OperatorResult(operator=operator, messages=[], provider_error=True,
                              error=f"the parent's change could not be checked out: {exc}")
    try:
        files = open_files(draft, ctx.open)
        lines = shown_lines(files, problem.block_start, problem.block_end)
        if lines > OPEN_LINES:  # the parent's diff grew them: refused before any call is paid for
            return failed(open_lines_problem(lines))
        block = marked_block(files, problem.block_start, problem.block_end)
        messages = tree_messages(
            config, parent=parent, result=parent_result, operator=operator, files=files,
            best_score=best_score, inspirations=inspirations, scratchpad=scratchpad,
            extra_instruction=extra_instruction, block=block, refusals=ctx.refusals,
            prefix=ctx.repo.prefix,
        )
        try:
            completion = provider.complete(
                messages, model=model_cfg.model, max_tokens=model_cfg.max_tokens,
                temperature=model_cfg.temperature,
            )
        except ProviderError as exc:  # billed what it cost, when it ran and said so
            return OperatorResult(operator=operator, messages=messages, error=str(exc), provider_error=True,
                                  completion=exc.completion)
        try:
            if block is not None:
                path, old = block
                new, mode = apply_response(old, completion.text)
                if new.count("\ufffd") > old.count("\ufffd") and for_model(old) != old:
                    # The model saw U+FFFD for a byte that is not UTF-8 and wrote it back.
                    raise DiffError("the reply rewrote a line holding a byte that is not UTF-8 "
                                    "(shown as U+FFFD); leave such lines as they are")
                target = draft.path / path
                text = target.read_bytes().decode("utf-8", errors="surrogateescape")
                spliced = splice_block(text, new, problem.block_start, problem.block_end)
                target.write_bytes(spliced.encode("utf-8", errors="surrogateescape"))
            else:
                mode = "diff"
                trees.apply_in(draft, parse_diff_response(completion.text),
                               directory=ctx.repo.prefix.rstrip("/"))
            child = trees.staged_diff(draft, ctx.base)
        except (DiffError, trees.TreeError) as exc:
            return failed(f"could not apply response: {exc}")
        if not child.strip():
            return failed("could not apply response: the reply changed nothing")
        outside = trees.touches_only(child, ctx.open, prefix=ctx.repo.prefix)
        if outside:
            return failed(f"changes files outside the open files: {', '.join(outside)} "
                          f"(only {', '.join(ctx.open)} may change)")
        return OperatorResult(operator=operator, messages=messages, completion=completion, block=child, mode=mode)
    finally:
        trees.remove(ctx.run_dir, ctx.draft_id)


_NOTHING_TO_SWEEP = (
    "param_lhs found no sweepable parameters: the block declares no "
    "`# PARAMS: {...}` line with matching module-level assignments"
)


def param_lhs(parent: Candidate, *, row: Mapping[str, Any]) -> OperatorResult:
    """Zero-LLM-cost parameter sweep over the block's declared `# PARAMS:`:
    the parent's block with its constants set to `row`, one row of the run's
    Latin hypercube over those ranges (the driver's `_next_lhs_row`, rounded by
    `lhs.rows_for_params`). A constant the row does not name keeps its value.

    Returns a failed result when the block declares nothing to sweep. The
    driver checks `params.has_params()` before routing a child here, so that
    branch only shows up when a candidate has dropped the declaration its
    parent had.
    """
    ranges = declared_ranges(parent.block)
    base = current_values(parent.block, set(ranges)) if ranges else {}
    if not base:
        return OperatorResult(operator="param_lhs", messages=[], error=_NOTHING_TO_SWEEP)
    values = {**base, **{name: value for name, value in row.items() if name in base}}
    return OperatorResult(
        operator="param_lhs",
        messages=[],
        block=apply_params(parent.block, values),
        mode="param_lhs",
        meta={"params": values},
    )


def param_lhs_typed(
    space: ParameterSpace, parent: Candidate, *, row: Mapping[str, Any], prior: Sequence[str] = ()
) -> OperatorResult:
    """The same sweep over a declared `problem.parameters` space: `row`, one
    row of the run's hypercube (`latin_rows`), rendered as the child's block,
    so it does not matter what shape the parent's code is in. `prior` names
    the parameters of the row drawn near a prior (`meta["prior"]`)."""
    values = dict(row)
    return OperatorResult(
        operator="param_lhs",
        messages=[],
        block=space.render_block(values),
        mode="param_lhs",
        meta={"params": values, **({"prior": list(prior)} if prior else {})},
    )


def latin_rows(space: ParameterSpace, count: int, rng: random.Random) -> list[tuple[dict, list[str]]]:
    """A Latin hypercube of `count` configurations over a declared space, each
    with the names of its parameters drawn near a prior.

    Types and scales come from the declaration, so a boolean is flipped rather
    than frozen, a choice is drawn from its values, and a log-scale parameter is
    covered decade by decade (`ParameterSpace.latin_hypercube`).

    A parameter with a `prior` (what an earlier study's winner suggests) is,
    in each row, drawn with the prior's weight as its chance from the band
    around the value instead (`PRIOR_BAND`; a bool or a choice takes the value
    itself). The other draws still cover the whole range: a prior never
    excludes a value. Without a prior nothing more is drawn from `rng` than
    the hypercube itself.
    """
    return _with_priors(space, space.latin_hypercube(count, rng), rng)


def _with_priors(space: ParameterSpace, variants: list[dict], rng: random.Random) -> list[tuple[dict, list[str]]]:
    """Each variant, with every parameter that has a prior drawn near its value
    at the prior's weight as its chance, and the names so drawn. Without a
    prior nothing is drawn from `rng`, so a space without one sweeps as before."""
    priored = [p for p in space.parameters if p.prior is not None]
    out = []
    for values in variants:
        names = []
        for parameter in priored:
            value, weight = parameter.prior  # type: ignore[misc]
            if rng.random() < weight:
                values[parameter.name] = _near(parameter, value, rng)
                names.append(parameter.name)
        out.append((values, names))
    return out


def _near(parameter: Parameter, value, rng: random.Random):
    """A value within `PRIOR_BAND` of `value` on the parameter's own scale. A
    draw past an end of the range is reflected back inside, not clamped onto
    the end, so a prior at an end does not pile half its draws on one point."""
    if not parameter.numeric:
        return value
    centre = parameter.to_unit(value)
    unit = centre + rng.uniform(-PRIOR_BAND, PRIOR_BAND)
    if unit < 0.0:
        unit = -unit
    elif unit > 1.0:
        unit = 2.0 - unit
    drawn = parameter.from_unit(unit)  # still clamps, for the last rounding
    if parameter.type == "int" and abs(parameter.to_unit(drawn) - centre) > PRIOR_BAND:
        drawn += 1 if drawn < value else -1  # rounded out of the band: the whole number next to it, inside
    return drawn


def _rendered(space: ParameterSpace, values: dict, operator: str, **meta) -> OperatorResult:
    return OperatorResult(
        operator=operator,
        messages=[],
        block=space.render_block(values),
        mode=operator,
        meta={"params": values, **meta},
    )


def param_local(
    space: ParameterSpace, parent: Candidate, *, seed: int, scale: float = STEP_SCALE
) -> OperatorResult:
    """A neighbour of the parent's configuration: one to three parameters moved
    a little (`ParameterSpace.perturb`). The exploiting half of a model-free
    search -- most of what can be won from a mature solver's defaults is next
    to them. Drawn again while it breaks a constraint (`ParameterSpace.draw`).

    From the starting point, each parameter with a `prior` starts, with the
    prior's weight as its chance, from the prior's value instead, and the step
    is taken from there (`meta["prior"]` names those parameters): the first
    local steps look near an earlier study's winner as well as near the
    starting point. From any other parent, and without a prior, nothing more is
    drawn."""
    base = dict(parent.params or space.defaults())
    rng = random.Random(seed)
    started: list[str] = []
    if parent.operator == SEED_OPERATOR:
        for parameter in space.parameters:
            if parameter.prior is not None and rng.random() < parameter.prior[1]:
                base[parameter.name] = parameter.prior[0]
                started.append(parameter.name)
    child = space.draw(lambda: space.perturb(base, rng, scale=scale))
    return _rendered(space, child, "param_local", **({"prior": started} if started else {}))


def param_cross(
    space: ParameterSpace,
    parent: Candidate,
    mate: Candidate | None,
    *,
    seed: int,
    scale: float = STEP_SCALE,
) -> OperatorResult:
    """Each parameter from the parent or from `mate`, so gains found separately
    are tried together. Without a second configuration worth crossing with --
    the first generations -- it is a local step instead, and says so."""
    rng = random.Random(seed)
    base = dict(parent.params or space.defaults())
    other = dict(mate.params) if mate is not None and mate.params else {}
    differing = [p.name for p in space if p.name in other and other[p.name] != base[p.name]]
    if len(differing) < 2:  # any cross of these two is one of the two
        child = space.draw(lambda: space.perturb(base, rng, scale=scale))
        return _rendered(space, child, "param_local", fallback_of="param_cross")

    def cross() -> dict:
        taken = [name for name in differing if rng.random() < 0.5]
        if not taken or len(taken) == len(differing):  # the coin fell the same way every time
            taken = rng.sample(differing, k=rng.randint(1, len(differing) - 1))
        return {**base, **{name: other[name] for name in taken}}

    return _rendered(space, space.draw(cross), "param_cross", mate_id=mate.id)


def param_tpe(
    space: ParameterSpace,
    parent: Candidate,
    observations: list[Observation],
    *,
    seed: int,
    taken: Sequence[Mapping[str, Any]] = (),
    scale: float = STEP_SCALE,
) -> OperatorResult:
    """The configuration a Tree-structured Parzen Estimator rates highest, given
    every configuration evaluated so far (`search/tuning.py`) and the siblings
    already proposed this generation (`taken`: a constant liar, counted bad).
    With too few observations to estimate anything it is a local step instead."""
    rng = random.Random(seed)
    proposal = propose_tpe(space, observations, rng, taken=taken)
    if proposal is None:
        base = dict(parent.params or space.defaults())
        child = space.draw(lambda: space.perturb(base, rng, scale=scale))
        return _rendered(space, child, "param_local", fallback_of="param_tpe")
    return _rendered(space, proposal, "param_tpe", observations=len(observations))

