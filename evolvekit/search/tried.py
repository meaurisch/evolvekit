"""What was tried on a parent: archive rows turned into ledger lines (A3.1).

Pure: archive rows in, `prompts.LedgerEntry` values out. Nothing here reads
the driver or draws a random number, so the ledger a resumed run shows is the
one the uninterrupted run would have shown -- the archive is rebuilt from
`runs.jsonl`, and the ledger is a function of the archive alone.

The outcome words say what the engine recorded about a child, in the engine's
own terms (the model reads them, not a person). They report a signed score
change against the parent as it stands now; they never call a change better or
worse.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence

from evolvekit.candidate import Candidate
from evolvekit.deltas import change_summary, failure_line
from evolvekit.prompts import LEDGER_LINES, LedgerEntry

__all__ = ["outcome_words", "entry_for", "parent_ledger", "score_text"]

_RACED = re.compile(r"raced out after (\d+)")
_DAILY_CAP = "daily full-evaluation cap reached"
_NOT_APPLIED = "could not apply response"
_COUNTER = re.compile(r"-c(\d+)$")


def score_text(value: float, *, signed: bool) -> str:
    """Six significant digits like the score breakdown's `.6g`, but written out
    without an exponent for ordinary magnitudes: `+0.00001`, `1234.5`."""
    sign = "+" if signed else ""
    if value == 0 or not math.isfinite(value) or not 1e-6 <= abs(value) < 1e15:
        return f"{value:{sign}.6g}"
    decimals = max(0, 5 - math.floor(math.log10(abs(value))))
    text = f"{value:{sign}.{decimals}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _first_line(text: str | None) -> str:
    for line in re.split(r"\r\n|\n|\r", text or ""):
        if line.strip():
            return re.sub(r"\s+", " ", line).strip()
    return ""


def _quoted(text: str) -> str:
    return "`" + text.replace("`", "'") + "`"


def _failed_apply(child: Candidate, parent: Candidate | None) -> bool:
    """The operator gave nothing to evaluate: the row keeps its parent's block."""
    return (
        child.rejected
        and child.novelty is None
        and child.behaviour_twin_id is None
        and parent is not None
        and child.block == parent.block
    )


def _suffixes(child: Candidate, parent: Candidate | None, best_id: str | None) -> str:
    text = " (now the best)" if best_id is not None and child.id == best_id else ""
    if child.novelty == "near":
        near = child.near_twin_id
        if near and parent is not None and near == parent.id:
            text += ", close to this parent"
        elif near:
            text += f", close to {near}"
    return text


def outcome_words(
    child: Candidate,
    parent: Candidate | None,
    *,
    best_id: str | None,
    twin: Candidate | None,
    final_stage: str,
) -> str:
    """What came of `child`, in words; the first shape that matches wins."""
    if _failed_apply(child, parent):
        reason = _first_line(child.reject_reason)
        if reason.startswith(_NOT_APPLIED):
            return f"not applied: {_quoted(reason)}"
        return f"no reply: {reason}" if reason else "no reply"
    if child.novelty == "no_op":
        return "rejected: no change from this parent"
    if child.novelty == "duplicate":
        return f"rejected: same program as {child.twin_id or 'an earlier candidate'}"
    if child.behaviour_twin_id:
        stage = child.stages_reached[-1] if child.stages_reached else None
        mine = child.behaviour_signatures.get(stage) if stage else None
        if twin is None or (mine is not None and twin.behaviour_signatures.get(stage) == mine):
            # Exact, or a twin this archive does not hold: the plain words are true either way.
            return f"same behaviour as {child.behaviour_twin_id}"
        return f"same behaviour as {child.behaviour_twin_id}, within the noise"
    if child.gated:
        return f"broke the gate {_quoted(_first_line(child.gated))}"
    if child.raced_out:
        match = _RACED.search(child.raced_out)
        return f"raced out after {match.group(1)} instances" if match else "raced out"
    if child.veto:
        return "kept out by the hold-out"
    if child.last_failure and _DAILY_CAP in _first_line(child.last_failure):
        return "not evaluated: the daily cap on final evaluations was reached"
    if child.last_failure:
        return f"failed: {_quoted(failure_line(child.last_failure))}"
    if not child.rejected and not child.competes and final_stage not in child.stages_reached:
        return "not promoted"
    fitness = child.fitness
    if not child.rejected and not child.competes and fitness is not None:
        return f"not ranked (score {score_text(fitness, signed=False)})"
    if not child.rejected and child.competes and fitness is not None:
        parent_fitness = parent.fitness if parent is not None else None
        if parent_fitness is not None:
            return score_text(fitness - parent_fitness, signed=True) + _suffixes(child, parent, best_id)
        return f"score {score_text(fitness, signed=False)}" + _suffixes(child, parent, best_id)
    if child.rejected and child.reject_reason:
        return f"rejected: {_quoted(_first_line(child.reject_reason))}"
    return "no score"


def _operator(child: Candidate) -> str:
    # A big step whose reply never applied is recorded as `big_step/big_step`.
    return "big_step" if child.operator == "big_step/big_step" else child.operator


def entry_for(
    child: Candidate,
    parent: Candidate | None,
    *,
    best_id: str | None,
    twin: Candidate | None,
    final_stage: str,
    tree: bool,
) -> LedgerEntry:
    """`child` as one ledger entry. A child that applied nothing has no change."""
    change = None
    if not _failed_apply(child, parent):
        change = change_summary(
            child.block,
            parent.block if parent is not None else None,
            None,
            params=child.params,
            parent_params=parent.params if parent is not None else None,
            tree=tree,
        )
    words = outcome_words(child, parent, best_id=best_id, twin=twin, final_stage=final_stage)
    return LedgerEntry(id=child.id, operator=_operator(child), change=change, outcome=words)


def _counter(candidate: Candidate) -> int:
    """The run's counter in `g<gen>-c<counter>`: ids sort wrongly as strings
    once the counter passes 9999."""
    match = _COUNTER.search(candidate.id)
    return int(match.group(1)) if match else -1


def parent_ledger(
    archive: Sequence[Candidate],
    parent: Candidate,
    *,
    by_id: Mapping[str, Candidate],
    best_id: str | None,
    final_stage: str,
    tree: bool,
    limit: int = LEDGER_LINES,
) -> tuple[LedgerEntry, ...]:
    """The last `limit` children of `parent`, newest first."""
    children = [c for c in archive if c.parent_id == parent.id and c.id != parent.id]
    children.sort(key=lambda c: (_counter(c), c.id), reverse=True)
    return tuple(
        entry_for(
            child, parent, best_id=best_id,
            twin=by_id.get(child.behaviour_twin_id) if child.behaviour_twin_id else None,
            final_stage=final_stage, tree=tree,
        )
        for child in children[:limit]
    )
