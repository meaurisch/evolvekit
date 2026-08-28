"""The `# PARAMS:` convention, and the zero-LLM-cost parameter operator.

A skeleton that wants its constants swept declares them once, inside the evolve
block, on a single line:

    # PARAMS: {"BONUS": [0.0, 40.0], "WIDTH": [4.0, 400.0]}
    BONUS = 20.0
    WIDTH = 128.0

`param_lhs` then reads the current values out of the block's module-level
assignments, draws a Latin-hypercube variant inside the declared ranges, and
rewrites those assignment lines. No model is called, so a param sweep costs
nothing but evaluator time -- which is the entire point of the operator.

The declaration is optional and the loop notices its absence: a skeleton with
no `# PARAMS:` line simply never routes a child to this operator. The
bin-packing example is deliberately param-less, so the sweep is exercised by a
synthetic skeleton in the tests instead of by a fake declaration in the demo.
"""

from __future__ import annotations

import ast
import re
from typing import Any, Mapping

from evolvekit.search.lhs import sweep_params

__all__ = [
    "PARAMS_MARKER",
    "declared_ranges",
    "current_values",
    "apply_params",
    "param_variant",
    "has_params",
]

PARAMS_MARKER = "# PARAMS:"

_PARAMS_RE = re.compile(r"^[ \t]*#[ \t]*PARAMS:[ \t]*(\{.*\})[ \t]*$", re.MULTILINE)


def declared_ranges(block: str) -> dict[str, list[float]]:
    """`{name: [lo, hi]}` from the block's `# PARAMS:` line, or `{}`."""
    match = _PARAMS_RE.search(block)
    if match is None:
        return {}
    try:
        raw = ast.literal_eval(match.group(1))
    except (ValueError, SyntaxError):
        return {}
    if not isinstance(raw, dict):
        return {}
    ranges: dict[str, list[float]] = {}
    for name, bounds in raw.items():
        if not isinstance(name, str) or not name.isidentifier():
            continue
        if not isinstance(bounds, (list, tuple)) or len(bounds) != 2:
            continue
        try:
            lo, hi = float(bounds[0]), float(bounds[1])
        except (TypeError, ValueError):
            continue
        if lo < hi:
            ranges[name] = [lo, hi]
    return ranges


def has_params(block: str) -> bool:
    return bool(declared_ranges(block))


def current_values(block: str, names: "set[str] | frozenset[str]") -> dict[str, Any]:
    """Module-level `NAME = <number>` assignments inside the block."""
    try:
        tree = ast.parse(block)
    except SyntaxError:
        return {}
    values: dict[str, Any] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or target.id not in names:
            continue
        try:
            value = ast.literal_eval(node.value)
        except (ValueError, SyntaxError):
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        values[target.id] = value
    return values


def apply_params(block: str, values: Mapping[str, Any]) -> str:
    """Rewrite the block's assignment lines to `values`. Nothing else moves."""
    lines = block.splitlines(keepends=True)
    for name, value in values.items():
        pattern = re.compile(rf"^([ \t]*{re.escape(name)}[ \t]*=[ \t]*)(.+?)([ \t]*)$")
        rendered = repr(value)
        for i, line in enumerate(lines):
            stripped = line.rstrip("\r\n")
            ending = line[len(stripped) :]
            match = pattern.match(stripped)
            if match is None:
                continue
            lines[i] = f"{match.group(1)}{rendered}{ending}"
            break
    return "".join(lines)


def param_variant(block: str, *, seed: int, n_variants: int = 6) -> tuple[str, dict[str, Any]] | None:
    """One swept variant of `block`, or None when there is nothing to sweep.

    Variant 0 of the sweep is always the base point, so it is skipped: a child
    identical to its parent is exactly what the novelty filter exists to stop.
    """
    ranges = declared_ranges(block)
    if not ranges:
        return None
    base = current_values(block, set(ranges))
    if not base:
        return None
    variants = sweep_params(base, ranges, max(2, n_variants), seed=seed)
    candidates = [v for v in variants[1:] if v != base]
    if not candidates:
        return None
    chosen = candidates[seed % len(candidates)]
    return apply_params(block, chosen), chosen
