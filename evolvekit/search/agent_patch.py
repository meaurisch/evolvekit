"""`agent_patch`: a coding agent as the mutation operator (design §5.4).

The parent's tree is checked out as a draft (`<run>/work/trees/<id>.draft`),
the brief is written to `<run>/work/briefs/<id>.draft/brief.md` -- outside
every tree, so never part of a diff, and in a folder of its own that the
agent may read -- and the provider's `act` runs the agent in the draft with a
short prompt that names the brief file (decision 39: the brief never goes on
a command line). The draft's diff against the base is the child's block
(cumulative): files the agent *added* outside the open files are dropped
from it (a scratch file, a cache) and named; a change to any other file
outside them refuses the child. The draft is removed again.

Every call is billed: as the provider reports it -- dollars (`claude-cli`),
requests (`copilot-cli`) -- even when it then failed; at its cap when it
reports nothing (a timeout, a Stop, a crash), recorded as `priced_from:
"cap"`. The cap is the smaller of what is left of `budget.max_usd` and
`models.<role>.options.act_max_usd` ($0.50 when not set), and is passed to
the tool as its spending limit. The token cap counts the uncached input and
the output only: the money cap bounds what an agent session spends.

Only imported for a tree problem (it uses `evolvekit.tree`).
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from pathlib import Path

from evolvekit.candidate import Candidate
from evolvekit.config import Config
from evolvekit.evaluate.types import EvalResult
from evolvekit.prompts import FAILURE_LIMIT
from evolvekit.providers.base import AgentError, Completion, Provider, ProviderError, supports_act
from evolvekit.search.operators import OperatorResult, TreeContext

__all__ = ["ACT_MAX_USD", "ACT_TIMEOUT_S", "BRIEF", "BRIEFS", "CAP", "IMPROVE", "agent_patch", "brief_path",
           "brief_prompt", "call_cap", "split_description", "write_brief"]

OPERATOR = "agent_patch"
BRIEFS = "briefs"
"""`<run>/work/briefs/<draft id>/brief.md`: one per agent call, kept as the record of what it was asked."""
BRIEF = "brief.md"
ACT_TIMEOUT_S = 1800.0
"""`models.<role>.options.act_timeout_s` when it is not set (decision D14)."""
ACT_MAX_USD = 0.50
"""`models.<role>.options.act_max_usd` when it is not set: one call's spending cap."""
CAP = "cap"
"""`priced_from` of a call that reported no cost and was charged its cap."""
IMPROVE = "What to improve:"
"""The line `compile_study` appends to a code study's description: `vary.code.brief`."""

PROMPT = (
    "Your instructions for this change are in the file {path}. Read it first, then change the files in "
    "this folder as it asks, and stop when you are done."
)


def brief_path(run_dir: Path, draft_id: str) -> Path:
    """Where one call's brief goes: a folder of its own, the only one outside the tree the agent may read."""
    return Path(run_dir) / "work" / BRIEFS / draft_id / BRIEF


def call_cap(options: dict, left_usd: float | None) -> float:
    """One call's spending cap: `act_max_usd` ($0.50 when not set), and never
    more than what is left of the budget."""
    cap = float(options.get("act_max_usd", ACT_MAX_USD))
    return max(0.0, cap if left_usd is None else min(cap, float(left_usd)))


def brief_prompt(path: Path) -> str:
    """The short prompt the agent gets: where the brief is, and "read it first"."""
    return PROMPT.format(path=path)


def split_description(description: str) -> tuple[str, str]:
    """`(the problem's description without its "What to improve:" line, that line's text)`."""
    kept: list[str] = []
    wants = ""
    for line in description.splitlines():
        if line.startswith(IMPROVE):
            wants = line[len(IMPROVE):].strip()
        else:
            kept.append(line)
    return "\n".join(kept).strip(), wants


INTERNAL = frozenset({"static_problems", "complexity"})
"""The static check's own counts: not measures of the program."""


def _measures(candidate: Candidate) -> str:
    shown = []
    for name, value in candidate.kpis.items():
        if name.startswith("_") or name.endswith("_raw") or name in INTERNAL:
            continue
        shown.append(f"{name} {value:g}" if isinstance(value, (int, float)) else f"{name} {value}")
    return ", ".join(shown)


def write_brief(
    path: Path,
    *,
    description: str,
    brief: str,
    open_files: Sequence[str],
    parent_diff: str,
    best: Candidate | None,
    worst: Candidate | None,
    failures: Sequence[str],
) -> str:
    """Write the agent's brief to `path` and return it: design §5.4's list, in order.
    Lone surrogates (bytes of an open file that are not UTF-8) become U+FFFD."""
    from evolvekit.tree_prompts import for_model

    measured = [f"- The {which} so far: {c.id} ({_measures(c)})"
                for which, c in (("best", best), ("worst", worst)) if c is not None]
    tails = "\n\n".join(failure[-FAILURE_LIMIT:] for failure in failures if failure) or "(none)"
    diff = parent_diff.rstrip("\n") or "(none: this is the starting point)"
    text = "\n".join([
        "# What to change",
        "",
        description.strip(),
        "",
        "## What the engineer wants",
        brief.strip() or "Improve the measure the study is about.",
        "",
        "## The files you may change",
        *(f"- {name}" for name in open_files),
        "Every path is relative to this folder. A change to any other file is refused.",
        "",
        "## Where the code is now",
        "This folder already holds these changes to the starting point:",
        "```diff",
        diff,
        "```",
        "",
        "## What has been measured",
        *(measured or ["Nothing yet."]),
        "",
        "## The last failures",
        "```",
        tails,
        "```",
        "",
        "## The rules",
        "- Change only the files listed above.",
        "- Keep the program's tests passing, and its command line and output as they are.",
        "- Do not run the benchmark yourself, and do not change how it is measured: evolvekit measures "
        "every change itself.",
        "- Leave no other files behind: delete any scratch file you create.",
        "- Do not commit. When you are done, stop: evolvekit reads your changes from this folder.",
        "",
    ])
    text = for_model(text)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return text


def _charged(exc: ProviderError, *, cap: float, model: str, provider: str) -> Completion | None:
    """What a failed call is billed: what it reported, its cap when it ran and
    reported nothing, nothing when it never ran (a plain `ProviderError`)."""
    if not isinstance(exc, AgentError):
        return None
    if exc.unreported and exc.usd is None and exc.requests is None:
        return Completion(text="", input_tokens=exc.input_tokens, output_tokens=exc.output_tokens, model=model,
                          provider=provider, usd=cap, raw={"priced_from": CAP, "failed": True})
    return Completion(text="", input_tokens=exc.input_tokens, output_tokens=exc.output_tokens, model=model,
                      provider=provider, usd=exc.usd, requests=exc.requests, raw={"failed": True})


def agent_patch(
    *,
    config: Config,
    provider: Provider,
    model_role: str,
    parent: Candidate,
    parent_result: EvalResult | None,
    context: TreeContext,
    best: Candidate | None,
    worst: Candidate | None,
    failures: Sequence[str],
    time_left_s: float | None = None,
    left_usd: float | None = None,
    cancel: threading.Event | None = None,
) -> OperatorResult:
    """One agent call on the parent's tree; its edits are the child's diff.

    The call's time limit is the smaller of `act_timeout_s` and `time_left_s`
    (the study's time left, decision 36); its spending cap the smaller of
    `act_max_usd` and `left_usd` (what is left of `budget.max_usd`), passed on
    to the tool; `cancel` (a Stop request) ends the agent's process tree. An
    agent stopped at a limit keeps its edits (D14). The result's `completion`
    is what the call is billed, failed or not; `meta["dropped"]` names the
    files the agent added outside the open files, left out of the diff."""
    del parent_result  # the brief shows the archive's best and worst instead
    from evolvekit import tree as trees

    if not supports_act(provider):
        return OperatorResult(
            operator=OPERATOR, messages=[], provider_error=True,
            error=(f"the {model_role} model's provider ({getattr(provider, 'name', provider)}) cannot act as a "
                   "coding agent; agent_patch needs claude-cli or copilot-cli"),
        )
    model_cfg = config.models.by_role(model_role)
    timeout = float(model_cfg.options.get("act_timeout_s", ACT_TIMEOUT_S))
    if time_left_s is not None:
        timeout = min(timeout, float(time_left_s))
    if timeout <= 0:
        return OperatorResult(operator=OPERATOR, messages=[], error="the study has no time left for the agent")
    cap = call_cap(model_cfg.options, left_usd)

    try:
        draft = trees.materialise(context.run_dir, repo=context.repo, base=context.base, diff=parent.block,
                                  candidate_id=context.draft_id)
    except trees.TreeError as exc:
        return OperatorResult(operator=OPERATOR, messages=[], provider_error=True,
                              error=f"the parent's change could not be checked out: {exc}")
    messages: list[dict[str, str]] = []
    completion: Completion | None = None
    meta: dict[str, object] = {}

    def failed(error: str) -> OperatorResult:
        return OperatorResult(operator=OPERATOR, messages=messages, completion=completion, error=error, meta=meta)

    try:
        description, wants = split_description(config.problem.description)
        path = brief_path(context.run_dir, context.draft_id)
        write_brief(path, description=description, brief=wants, open_files=context.open,
                    parent_diff=trees.relative_diff(parent.block, context.repo.prefix), best=best, worst=worst,
                    failures=failures)
        prompt = brief_prompt(path)
        messages = [{"role": "user", "content": prompt}]
        try:
            outcome = provider.act(prompt, cwd=draft.path, model=model_cfg.model, timeout_s=timeout,  # type: ignore[attr-defined]
                                   read_dirs=(path.parent,), max_usd=cap, cancel=cancel)
        except ProviderError as exc:
            return OperatorResult(operator=OPERATOR, messages=messages, error=str(exc), provider_error=True,
                                  completion=_charged(exc, cap=cap, model=model_cfg.model, provider=provider.name))
        raw: dict[str, object] = {"stopped_at_limit": not outcome.ok,
                                  "cached_input_tokens": outcome.cached_input_tokens}
        usd = outcome.usd
        if usd is None and outcome.requests is None:  # no cost came back: the cap is what it may have spent
            usd, raw["priced_from"] = cap, CAP
        completion = Completion(
            text=outcome.text, input_tokens=outcome.input_tokens, output_tokens=outcome.output_tokens,
            model=model_cfg.model, provider=provider.name, usd=usd, requests=outcome.requests, raw=raw,
        )
        try:
            child, dropped = trees.staged_diff_without_strays(draft, context.base, context.open,
                                                              prefix=context.repo.prefix)
            # Against the parent's own commit: the child's block holds the parent's change too.
            changed = trees.git(draft.worktree, "diff", "--cached", "--name-only", draft.commit).strip()
        except trees.TreeError as exc:
            return failed(f"the agent's change could not be read: {exc}")
        if dropped:
            meta["dropped"] = dropped
        if not changed:
            return failed("the agent changed nothing" + (_dropped_note(dropped) if dropped else ""))
        if not child.strip():
            return failed("the agent's edits put the starting point back as it was")
        outside = trees.touches_only(child, context.open, prefix=context.repo.prefix)
        if outside:
            return failed(f"changes files outside the open files: {', '.join(outside)} "
                          f"(only {', '.join(context.open)} may change)")
        return OperatorResult(operator=OPERATOR, messages=messages, completion=completion, block=child,
                              mode="agent", meta=meta)
    finally:
        trees.remove(context.run_dir, context.draft_id)


def _dropped_note(dropped: Sequence[str]) -> str:
    return f" (the files it added outside the open files were left out: {', '.join(dropped)})"
