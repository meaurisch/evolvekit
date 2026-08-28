"""The prompt skeleton: a stable prefix and a bounded body.

Two messages, always in this shape:

    system  role + protocol + problem context + the frozen skeleton
            -- byte-identical across a whole run, so prefix caching works
    user    parent block + score breakdown + last failure + operator instruction
            -- bounded; nothing here grows with the number of generations

Deliberately absent, per the post-mortem: verbatim source of the top-3
candidates, a 400-line brief, and a separate reflection call. The feedback the
model gets is the parent's own score breakdown and the last failure artefact.
"""

from __future__ import annotations

from dataclasses import dataclass

from evolvekit.candidate import Candidate
from evolvekit.config import Config
from evolvekit.evaluate.types import EvalResult

__all__ = [
    "build_messages",
    "system_prompt",
    "user_prompt",
    "Inspiration",
    "OPERATOR_INSTRUCTIONS",
    "FAILURE_LIMIT",
    "FEEDBACK_LIMIT",
    "FEEDBACK_LINES",
    "SCRATCHPAD_LIMIT",
    "latest_feedback",
]

FAILURE_LIMIT = 2000

FEEDBACK_LIMIT = 2000
FEEDBACK_LINES = 20
"""The evaluator's own note, bounded twice: the framework already truncated it
to 2,000 characters when it read the stage output, and the prompt takes at most
twenty lines of that. Bounded on both axes because the size of a run's prompts
must not depend on how chatty somebody's evaluator is feeling."""

SCRATCHPAD_LIMIT = 40
"""The meta-scratchpad is bounded in lines, not characters, because the whole
point of it is that it stays a short list of lessons."""


@dataclass(frozen=True)
class Inspiration:
    """An elite from another cell, offered to the prompt.

    Only `crossover` carries `block`; every other operator sees the delta
    summary alone. That is the proposal's rule -- "what changed and why, not
    the code" -- with the one exception that crossover cannot be performed
    without seeing both sides.
    """

    id: str
    summary: str
    score_line: str
    block: str | None = None

_PROTOCOL = """\
You improve one fenced region of a Python file. The rest of the file is fixed \
and you must not restate it.

Hard rules:
- Return ONLY the new contents of the evolve block, in the requested format.
- Keep every function the block already defines, with the same names and \
signatures; the fixed skeleton calls them.
- Standard library only. No I/O, no imports of os/sys/subprocess/pathlib.
- No explanation outside the code or diff blocks. No commentary, no preamble.
"""

_DIFF_FORMAT = """\
Reply with one or more SEARCH/REPLACE blocks and nothing else:

<<<<<<< SEARCH
(text copied exactly from the current block)
=======
(the replacement text)
>>>>>>> REPLACE

The SEARCH text must match the current block character for character. Prefer \
two or three small, surgical blocks over one large one.
"""

_REWRITE_FORMAT = """\
Reply with the complete new block inside a single ```python fence and nothing \
else.
"""

OPERATOR_INSTRUCTIONS = {
    "diff": (
        "Make one focused improvement to the heuristic. Change as little as "
        "possible while plausibly improving the score."
    ),
    "rewrite": (
        "Rewrite the block. Keep what evidently works in the parent and replace "
        "the part you believe is limiting the score."
    ),
    "big_step": (
        "This is a scheduled big step: the search has plateaued. Propose a "
        "structurally different heuristic rather than a tweak of the parent. "
        "Reason about why the parent's scoring rule mis-ranks its options, then "
        "write a rule based on a different principle."
    ),
    "crossover": (
        "Combine the two blocks below. Take the mechanism that evidently works "
        "in the current block and the idea that evidently works in the "
        "inspiration, and write one block that has both. It must be a genuine "
        "combination, not either parent restated."
    ),
}


def _bullet_section(title: str, items: "tuple[str, ...] | list[str]") -> str:
    """One optional `## title` section built from a bullet list, or nothing.

    Everything here comes from `problem:` in the config, which does not change
    during a run, so the section is as cacheable as the rest of the system
    prompt. An empty list renders nothing at all rather than an empty heading:
    a section saying "- none" is a section the model has to read.
    """
    if not items:
        return ""
    body = "\n".join(f"- {item}" for item in items)
    return f"\n## {title}\n{body}\n"


def _prose_section(title: str, text: str) -> str:
    body = text.strip()
    return f"\n## {title}\n{body}\n" if body else ""


def system_prompt(config: Config, skeleton_prefix: str, skeleton_suffix: str) -> str:
    """The cache-friendly half. Identical for every call in a run."""
    problem = config.problem
    description = problem.description.strip() or (
        "Improve the evolve block so the evaluator's score goes up."
    )
    # Named, headed sections rather than sentences buried in the description.
    # The second Phase C circle-packing run is the reason: every one of its
    # fifteen candidates was a hand-placed arrangement, because the one
    # sentence that mentioned the skeleton's time-bounded-search helpers sat in
    # the middle of a paragraph about the referee.
    tools = _bullet_section("Tools your block may call and rely on", problem.tools)
    constraints = _bullet_section("Constraints", problem.constraints)
    novelty = _prose_section("What counts as a new candidate here", problem.what_counts_as_new)
    objective = config.evaluate.score
    direction = "higher is better" if objective.direction == "maximize" else "lower is better"
    penalties = (
        "\n".join(
            f"- {p.kpi} (weight {p.weight:g}, {p.scale}) is subtracted from the score"
            for p in config.evaluate.penalties
        )
        or "- none"
    )
    return f"""\
You are an optimisation engineer evolving a single heuristic.

## Problem
{description}
{tools}{constraints}{novelty}
## Scoring
Objective KPI: `{objective.objective}` ({direction}). Reported score is always \
maximised, so a larger reported score is always better.
Penalties (violations lower the score, they never invalidate a candidate):
{penalties}

## The fixed skeleton (you may NOT change this)
```python
{skeleton_prefix.rstrip()}
    ...your block goes here...
{skeleton_suffix.rstrip()}
```

## Protocol
{_PROTOCOL}"""


def _score_breakdown(result: EvalResult | None, parent: Candidate) -> str:
    score = result.score if result is not None else parent.score
    if score is None:
        return "No score yet -- this is the first candidate."
    lines = [f"score: {score:.6g} (higher is better)"]
    kpis = result.kpis if result is not None else parent.kpis
    if kpis:
        lines.append(
            "kpis: "
            + ", ".join(f"{k}={v:.6g}" for k, v in sorted(kpis.items()))
        )
    terms = result.penalty_terms if result is not None else {}
    active = {k: v for k, v in terms.items() if v}
    if active:
        lines.append(
            "penalties applied: "
            + ", ".join(f"-{v:.6g} from {k}" for k, v in sorted(active.items()))
        )
    stages = result.stages_reached if result is not None else parent.stages_reached
    if stages:
        lines.append(f"stages reached: {', '.join(stages)}")
    gap = result.generalization_gap if result is not None else parent.generalization_gap
    if gap is not None:
        lines.append(f"public-minus-private gap: {gap:+.6g}")
    return "\n".join(lines)


def latest_feedback(result: EvalResult | None, parent: Candidate) -> str:
    """The deepest stage's `text_feedback` for this parent, or `""`.

    Deepest rather than first: the proxy stage's note is about five instances
    and the full stage's about twenty, so if the candidate got as far as the
    second one then the second one is the truth. `stages_reached` is already in
    cascade order, which makes the answer a reverse scan over it.
    """
    feedback = (result.feedback if result is not None else parent.feedback) or {}
    if not feedback:
        return ""
    stages = (
        result.stages_reached if result is not None else parent.stages_reached
    ) or []
    for stage_id in reversed(stages):
        note = feedback.get(stage_id, "").strip()
        if note:
            return note
    # No stage list -- a record written before stages were logged, say. Take
    # whatever there is rather than dropping a note that exists.
    return next((v.strip() for v in feedback.values() if v.strip()), "")


def _feedback_section(result: EvalResult | None, parent: Candidate) -> str:
    """What the evaluator said about this parent, in the evaluator's own words.

    The score breakdown above it is a vector of numbers, and a vector of
    numbers cannot say "you lost on the Weibull instances" or "you used 0.1 %
    of your time budget". OpenEvolve found stage artefacts to be the single
    most useful thing a prompt can carry; this is the same channel for a stage
    that *succeeded*.
    """
    note = latest_feedback(result, parent)
    if not note:
        return ""
    body = "\n".join(note.splitlines()[:FEEDBACK_LINES])[:FEEDBACK_LIMIT]
    return f"\n## Evaluator notes\n{body}\n"


def _inspiration_section(inspirations: "list[Inspiration] | tuple[Inspiration, ...]") -> str:
    if not inspirations:
        return ""
    lines = ["\n## Other candidates in the archive (do not copy them verbatim)"]
    for item in inspirations:
        lines.append(f"- `{item.id}` -- {item.summary} [{item.score_line}]")
        if item.block is not None:
            lines.append(f"\n```python\n{item.block.rstrip()}\n```\n")
    return "\n".join(lines) + "\n"


def _scratchpad_section(scratchpad: str | None) -> str:
    if not scratchpad or not scratchpad.strip():
        return ""
    lines = scratchpad.strip().splitlines()[:SCRATCHPAD_LIMIT]
    return "\n## Notes from earlier generations\n" + "\n".join(lines) + "\n"


def user_prompt(
    parent: Candidate,
    result: EvalResult | None,
    operator: str,
    *,
    best_score: float | None = None,
    inspirations: "list[Inspiration] | tuple[Inspiration, ...]" = (),
    scratchpad: str | None = None,
    extra_instruction: str | None = None,
) -> str:
    """The bounded half. Size does not grow with the run."""
    instruction = OPERATOR_INSTRUCTIONS.get(
        operator, OPERATOR_INSTRUCTIONS["rewrite"]
    )
    if extra_instruction:
        instruction = f"{instruction}\n\n{extra_instruction.strip()}"
    fmt = _DIFF_FORMAT if operator == "diff" else _REWRITE_FORMAT

    failure = (result.last_failure if result is not None else parent.last_failure) or ""
    failure_section = (
        f"\n## Last failure artefact (truncated)\n```\n{failure[-FAILURE_LIMIT:]}\n```\n"
        if failure.strip()
        else ""
    )
    context = ""
    if best_score is not None and (result.score if result else parent.score) is not None:
        current = result.score if result else parent.score
        assert current is not None
        delta = current - best_score
        context = (
            f"\nBest score in the archive so far: {best_score:.6g} "
            f"(this parent is {delta:+.6g} against it).\n"
        )

    return f"""\
## Current block (generation {parent.generation}, operator that produced it: {parent.operator})
```python
{parent.block.rstrip()}
```

## How it scored
{_score_breakdown(result, parent)}
{_feedback_section(result, parent)}\
{context}{_inspiration_section(inspirations)}{_scratchpad_section(scratchpad)}\
{failure_section}
## Your task
{instruction}

{fmt}"""


def build_messages(
    config: Config,
    *,
    skeleton_prefix: str,
    skeleton_suffix: str,
    parent: Candidate,
    result: EvalResult | None,
    operator: str,
    best_score: float | None = None,
    inspirations: "list[Inspiration] | tuple[Inspiration, ...]" = (),
    scratchpad: str | None = None,
    extra_instruction: str | None = None,
) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": system_prompt(config, skeleton_prefix, skeleton_suffix),
        },
        {
            "role": "user",
            "content": user_prompt(
                parent,
                result,
                operator,
                best_score=best_score,
                inspirations=inspirations,
                scratchpad=scratchpad,
                extra_instruction=extra_instruction,
            ),
        },
    ]
