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

from evolvekit.candidate import Candidate
from evolvekit.config import Config
from evolvekit.diffs import DiffError, apply_response
from evolvekit.evaluate.types import EvalResult
from evolvekit.prompts import Inspiration, build_messages
from evolvekit.providers.base import Completion, Provider, ProviderError
from evolvekit.search.params import param_variant
from evolvekit.search.tuning import Observation, propose_tpe
from evolvekit.space import ParameterSpace

__all__ = [
    "OperatorResult",
    "run_operator",
    "param_lhs",
    "param_lhs_typed",
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
) -> OperatorResult:
    """One LLM call, one apply attempt."""
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
    except ProviderError as exc:
        return OperatorResult(
            operator=operator, messages=messages, error=str(exc), provider_error=True
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


def param_lhs(parent: Candidate, *, seed: int, n_variants: int = 6) -> OperatorResult:
    """Zero-LLM-cost parameter sweep over the block's declared `# PARAMS:`.

    Returns a failed result when the block declares nothing to sweep. The
    driver checks `params.has_params()` before routing a child here, so that
    branch only shows up when a candidate has dropped the declaration its
    parent had.
    """
    variant = param_variant(parent.block, seed=seed, n_variants=n_variants)
    if variant is None:
        return OperatorResult(
            operator="param_lhs",
            messages=[],
            error=(
                "param_lhs found no sweepable parameters: the block declares no "
                "`# PARAMS: {...}` line with matching module-level assignments"
            ),
        )
    block, values = variant
    return OperatorResult(
        operator="param_lhs",
        messages=[],
        block=block,
        mode="param_lhs",
        meta={"params": values},
    )


def param_lhs_typed(
    space: ParameterSpace, parent: Candidate, *, seed: int, n_variants: int = 6
) -> OperatorResult:
    """The same sweep over a declared `problem.parameters` space.

    Types and scales come from the declaration, so a boolean is flipped rather
    than frozen, a choice is drawn from its values, and a log-scale parameter is
    covered decade by decade. The child's block is *rendered* from the sampled
    values, so it does not matter what shape the parent's code is in.
    """
    rng = random.Random(seed)
    base = parent.params or space.defaults()
    variants = [v for v in space.latin_hypercube(max(2, n_variants), rng) if v != base]
    if not variants:  # a space of one point
        return OperatorResult(
            operator="param_lhs",
            messages=[],
            error="param_lhs found nothing to sweep: every sample equals the parent",
        )
    values = variants[seed % len(variants)]
    return OperatorResult(
        operator="param_lhs",
        messages=[],
        block=space.render_block(values),
        mode="param_lhs",
        meta={"params": values},
    )


def _rendered(space: ParameterSpace, values: dict, operator: str, **meta) -> OperatorResult:
    return OperatorResult(
        operator=operator,
        messages=[],
        block=space.render_block(values),
        mode=operator,
        meta={"params": values, **meta},
    )


def param_local(space: ParameterSpace, parent: Candidate, *, seed: int) -> OperatorResult:
    """A neighbour of the parent's configuration: one to three parameters moved
    a little (`ParameterSpace.perturb`). The exploiting half of a model-free
    search -- most of what can be won from a mature solver's defaults is next
    to them."""
    base = dict(parent.params or space.defaults())
    return _rendered(space, space.perturb(base, random.Random(seed)), "param_local")


def param_cross(
    space: ParameterSpace, parent: Candidate, mate: Candidate | None, *, seed: int
) -> OperatorResult:
    """Each parameter from the parent or from `mate`, so gains found separately
    are tried together. Without a second configuration worth crossing with --
    the first generations -- it is a local step instead, and says so."""
    rng = random.Random(seed)
    base = dict(parent.params or space.defaults())
    other = dict(mate.params) if mate is not None and mate.params else {}
    differing = [p.name for p in space if p.name in other and other[p.name] != base[p.name]]
    if len(differing) < 2:  # any cross of these two is one of the two
        return _rendered(space, space.perturb(base, rng), "param_local", fallback_of="param_cross")
    taken = [name for name in differing if rng.random() < 0.5]
    if not taken or len(taken) == len(differing):  # the coin fell the same way every time
        taken = rng.sample(differing, k=rng.randint(1, len(differing) - 1))
    child = {**base, **{name: other[name] for name in taken}}
    return _rendered(space, child, "param_cross", mate_id=mate.id)


def param_tpe(
    space: ParameterSpace,
    parent: Candidate,
    observations: list[Observation],
    *,
    seed: int,
) -> OperatorResult:
    """The configuration a Tree-structured Parzen Estimator rates highest, given
    every configuration evaluated so far (`search/tuning.py`). With too few
    observations to estimate anything it is a local step instead."""
    rng = random.Random(seed)
    proposal = propose_tpe(space, observations, rng)
    if proposal is None:
        base = dict(parent.params or space.defaults())
        return _rendered(space, space.perturb(base, rng), "param_local", fallback_of="param_tpe")
    return _rendered(space, proposal, "param_tpe", observations=len(observations))

