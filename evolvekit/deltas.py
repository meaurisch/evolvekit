"""One-line, LLM-free summaries of what a candidate changed.

DeltaEvolve's finding, and the post-mortem's: what the next prompt needs is
*what changed and why*, not another verbatim module. A delta summary here is
two facts glued together, both free:

    +6/-2 lines vs g001-c0002 -- Best fit with a bonus for a usable residual.

the unified-diff stat against the candidate's own parent, and the first
paragraph of the block's first docstring. No model is asked to write it, so it
costs nothing and cannot hallucinate.
"""

from __future__ import annotations

import ast
import difflib
import re

__all__ = ["diff_stat", "first_docstring", "delta_summary"]

SUMMARY_LIMIT = 160


def diff_stat(old: str, new: str) -> tuple[int, int]:
    """`(added, removed)` line counts of a unified diff between two blocks."""
    added = removed = 0
    diff = difflib.unified_diff(
        old.splitlines(), new.splitlines(), lineterm="", n=0
    )
    for line in diff:
        if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return added, removed


def first_docstring(block: str) -> str:
    """The first paragraph of the block's first docstring, on one line."""
    try:
        tree = ast.parse(block)
    except SyntaxError:
        return ""
    text = ast.get_docstring(tree) or ""
    if not text:
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                text = ast.get_docstring(node) or ""
                if text:
                    break
    if not text:
        return ""
    paragraph = text.strip().split("\n\n", 1)[0]
    return re.sub(r"\s+", " ", paragraph).strip()


def delta_summary(
    block: str, parent_block: str | None, parent_id: str | None
) -> str:
    """One line: how much changed, against whom, and what it claims to do."""
    parts: list[str] = []
    if parent_block is not None:
        added, removed = diff_stat(parent_block, block)
        against = f" vs {parent_id}" if parent_id else ""
        parts.append(f"+{added}/-{removed} lines{against}")
    doc = first_docstring(block)
    if doc:
        parts.append(doc)
    summary = " -- ".join(parts) if parts else "no recorded change"
    return summary[:SUMMARY_LIMIT]
