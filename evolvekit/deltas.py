"""One-line, LLM-free summaries of what a candidate changed.

DeltaEvolve's finding, and the post-mortem's: what the next prompt needs is
*what changed*, not another verbatim module. A change summary here is two local
facts glued together, both free:

    +6/-2 lines vs g001-c0002; functions: priority; constants: BONUS

the unified-diff stat against the candidate's own parent, and the names of the
top-level functions and literal constants whose code differs (read from the
AST). On a tree problem, where the block is a diff, the second fact is the files
whose part of the diff changed.

The block's docstring is deliberately not one of the facts any more: a model's
docstring can describe the rule it meant rather than the one it wrote, and a
summary that repeats it tells the next prompt what was intended, not what was
tried. No model is asked to write a summary, so it costs nothing and cannot
hallucinate.
"""

from __future__ import annotations

import ast
import copy
import difflib
import re

__all__ = ["diff_stat", "changed_names", "change_summary", "failure_line"]

SUMMARY_LIMIT = 160
NAMES_SHOWN = 4

_MISSING = object()


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


def _parse(block: str) -> ast.Module | None:
    try:
        return ast.parse(block)
    except (SyntaxError, ValueError):  # ValueError: a null byte, or a lone surrogate
        return None


class _BlankNumbers(ast.NodeTransformer):
    """Every numeric literal blanked (not strings, not booleans): what is left
    is the function's shape, so equal dumps mean "only numbers changed"."""

    def visit_Constant(self, node: ast.Constant) -> ast.AST:
        value = node.value
        if isinstance(value, (int, float, complex)) and not isinstance(value, bool):
            return ast.copy_location(ast.Constant(value=None), node)
        return node


def _functions(tree: ast.Module) -> dict[str, ast.AST]:
    """Top-level functions, and methods as `Class.method`, in block order. A
    nested def is part of its outer function's dump."""
    found: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found.setdefault(node.name, node)
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    found.setdefault(f"{node.name}.{item.name}", item)
    return found


def _constants(tree: ast.Module) -> dict[str, tuple[bool, str]]:
    """Block-level `NAME = <expr>` / `NAME: T = <expr>` with one `Name` target:
    name -> (its value is a literal, a comparable form of the value)."""
    found: dict[str, tuple[bool, str]] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target, value = node.target, node.value
        else:
            continue
        if not isinstance(target, ast.Name):
            continue
        try:
            literal = ast.literal_eval(value)
        except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
            found[target.id] = (False, ast.dump(value))
        else:
            found[target.id] = (True, f"{type(literal).__name__}:{literal!r}")
    return found


def _compare(child: dict, parent: dict, same, mark=None) -> list[str]:
    """Changed names in the child's order, then `+added` (child's order), then
    `-removed` (parent's order)."""
    changed = []
    for name, value in child.items():
        old = parent.get(name, _MISSING)
        if old is not _MISSING and not same(old, value):
            changed.append(mark(name, old, value) if mark else name)
    added = [f"+{name}" for name in child if name not in parent]
    removed = [f"-{name}" for name in parent if name not in child]
    return changed + added + removed


def changed_names(old: str, new: str) -> tuple[list[str], list[str]] | None:
    """`(functions, constants)` that differ from `old` to `new`, or `None` when
    either side does not parse.

    A function whose code differs only in its numbers is marked
    `name (numbers only)` (A0's constants-only definition). A docstring is part
    of a function's code, so a docstring-only edit names the function too.
    Constants are the block-level names bound to a literal on either side."""
    old_tree, new_tree = _parse(old), _parse(new)
    if old_tree is None or new_tree is None:
        return None

    def numbers_only(name: str, a: ast.AST, b: ast.AST) -> str:
        blank = _BlankNumbers()
        if ast.dump(blank.visit(copy.deepcopy(a))) == ast.dump(blank.visit(copy.deepcopy(b))):
            return f"{name} (numbers only)"
        return name

    functions = _compare(
        _functions(new_tree), _functions(old_tree),
        lambda a, b: ast.dump(a) == ast.dump(b), numbers_only,
    )
    old_consts, new_consts = _constants(old_tree), _constants(new_tree)
    literal = {k for k, (is_lit, _) in old_consts.items() if is_lit} | {
        k for k, (is_lit, _) in new_consts.items() if is_lit
    }
    constants = _compare(
        {k: v for k, v in new_consts.items() if k in literal},
        {k: v for k, v in old_consts.items() if k in literal},
        lambda a, b: a == b,
    )
    return functions, constants


def _listed(label: str, names: list[str]) -> str:
    shown = ", ".join(names[:NAMES_SHOWN])
    if len(names) > NAMES_SHOWN:
        shown += f" and {len(names) - NAMES_SHOWN} more"
    return f"{label}: {shown}"


_GIT_HEADER = re.compile(r"^diff --git ", re.MULTILINE)


def _diff_sections(diff: str) -> dict[str, str]:
    """A unified diff's sections keyed on their `diff --git` header line, so a
    deletion (`+++ /dev/null`) and a rename are one file each."""
    sections: dict[str, str] = {}
    starts = [m.start() for m in _GIT_HEADER.finditer(diff)]
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(diff)
        text = diff[start:end]
        header = text.split("\n", 1)[0].rstrip("\r")
        sections.setdefault(header, text)
    return sections


def _header_path(header: str) -> str:
    """The file a `diff --git a/X b/Y` header names: its `b/` side."""
    rest = header[len("diff --git "):]
    half = (len(rest) - 1) // 2
    if len(rest) % 2 == 1 and rest[half] == " " and rest[:2] == "a/" and rest[half + 1:half + 3] == "b/" \
            and rest[2:half] == rest[half + 3:]:
        return rest[2:half]
    match = re.match(r'^"?a/(.*?)"? "?b/(.*?)"?$', rest)
    return match.group(2) if match else rest


def _tree_files(block: str, parent_block: str) -> list[str]:
    new, old = _diff_sections(block), _diff_sections(parent_block)
    changed = [h for h, text in new.items() if old.get(h) != text]
    changed += [h for h in old if h not in new]
    return [_header_path(h) for h in changed]


def change_summary(
    block: str,
    parent_block: str | None,
    parent_id: str | None = None,
    *,
    params: dict | None = None,
    parent_params: dict | None = None,
    tree: bool = False,
) -> str:
    """One line: how much changed, against whom, and which names it touched."""
    if parent_block is None:
        return "no recorded change"
    added, removed = diff_stat(parent_block, block)
    against = f" vs {parent_id}" if parent_id else ""
    if tree:
        summary = f"+{added}/-{removed} diff lines{against}"
        files = _tree_files(block, parent_block)
        if files:
            summary += "; " + _listed("files", files)
        return _bounded(summary)
    summary = f"+{added}/-{removed} lines{against}"
    names = changed_names(parent_block, block)
    if names is None:
        return _bounded(summary)
    old_tree, new_tree = _parse(parent_block), _parse(block)
    if ast.dump(old_tree) == ast.dump(new_tree):
        return _bounded(summary + "; comments or layout only")
    functions, constants = names
    if isinstance(params, dict) and isinstance(parent_params, dict):
        constants = [k for k, v in params.items() if parent_params.get(k, _MISSING) != v]
        constants += [f"-{k}" for k in parent_params if k not in params]
    parts = []
    if functions:
        parts.append(_listed("functions", functions))
    if constants:
        parts.append(_listed("constants", constants))
    if not parts:
        parts.append("other top-level code")
    return _bounded("; ".join([summary, *parts]))


def _bounded(summary: str) -> str:
    if len(summary) <= SUMMARY_LIMIT:
        return summary
    return summary[: SUMMARY_LIMIT - 3] + "..."


_STAGE_LEAD = re.compile(r"^stage [^:\s]+: ")


def failure_line(last_failure: str) -> str:
    """The one line of a `last_failure` artefact (`"stage <id>: <failure>\\n<stderr
    tail>"`) that says what went wrong: the last non-empty line of the whole
    text -- a Python traceback's exception line -- or, when the artefact is one
    line, the failure after `stage <id>: `. Backticks become `'`; never a newline."""
    # Lines are split at `\n` / `\r` only: a U+2028 or `\x85` inside a failure is
    # whitespace within its line, collapsed below, not a line of its own.
    lines = [line.strip() for line in re.split(r"\r\n|\n|\r", last_failure or "") if line.strip()]
    if not lines:
        return ""
    line = lines[-1]
    if len(lines) == 1:
        line = _STAGE_LEAD.sub("", line, count=1)
    return re.sub(r"\s+", " ", line.replace("`", "'")).strip()
