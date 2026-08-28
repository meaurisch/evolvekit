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

from dataclasses import dataclass, field

from evolvekit.candidate import Candidate
from evolvekit.config import Config
from evolvekit.diffs import DiffError, apply_response
from evolvekit.evaluate.types import EvalResult
from evolvekit.prompts import Inspiration, build_messages
from evolvekit.providers.base import Completion, Provider, ProviderError
from evolvekit.search.params import param_variant

__all__ = ["OperatorResult", "run_operator", "param_lhs", "OPERATOR_ROLES"]

OPERATOR_ROLES = {
    "diff": "small",
    "rewrite": "small",
    "crossover": "small",
    "param_lhs": "none",
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
