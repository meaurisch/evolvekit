"""SEARCH/REPLACE block parsing, with a full-rewrite fallback.

The `diff` operator asks for

    <<<<<<< SEARCH
    exact text from the parent block
    =======
    replacement text
    >>>>>>> REPLACE

which keeps prompts and completions small and preserves working code. Models
ignore that instruction often enough that a fenced code block is always
accepted as a whole-block rewrite instead -- the fallback is the feature, not
an apology for one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["DiffError", "SearchReplace", "parse_search_replace", "apply_response"]

_BLOCK_RE = re.compile(
    r"^<{5,9} *SEARCH[^\n]*\n(.*?)^={5,9}[^\n]*\n(.*?)^>{5,9} *REPLACE[^\n]*$",
    re.DOTALL | re.MULTILINE,
)
_FENCE_RE = re.compile(r"```[a-zA-Z0-9_+-]*\n(.*?)```", re.DOTALL)


class DiffError(ValueError):
    """A response could not be turned into a new block."""


@dataclass(frozen=True)
class SearchReplace:
    search: str
    replace: str


def parse_search_replace(response: str) -> list[SearchReplace]:
    """Extract every SEARCH/REPLACE pair from a completion, in order."""
    return [
        SearchReplace(search=m.group(1), replace=m.group(2))
        for m in _BLOCK_RE.finditer(response)
    ]


def _extract_rewrite(response: str) -> str | None:
    """The largest fenced code block, or the bare response if it looks like code."""
    fences = [m.group(1) for m in _FENCE_RE.finditer(response)]
    if fences:
        return max(fences, key=len)
    stripped = response.strip()
    if not stripped:
        return None
    # No fence: accept it only if it plausibly is code, not commentary.
    if re.search(r"^\s*(def |class |import |from |@)", stripped, re.MULTILINE):
        return stripped
    return None


def apply_response(block: str, response: str) -> tuple[str, str]:
    """Turn a completion into a new block.

    Returns `(new_block, mode)` where mode is `"diff"` or `"rewrite"`. Raises
    `DiffError` when neither path yields a usable, changed block.
    """
    edits = parse_search_replace(response)
    if edits:
        try:
            return _apply_edits(block, edits), "diff"
        except DiffError as exc:
            fallback = _extract_rewrite(response)
            if fallback is None:
                raise
            if fallback.strip() == block.strip():
                raise DiffError(f"{exc}; the rewrite fallback was identical") from exc
            return fallback, "rewrite"

    rewrite = _extract_rewrite(response)
    if rewrite is None:
        raise DiffError(
            "response contained neither a SEARCH/REPLACE block nor a code fence"
        )
    if rewrite.strip() == block.strip():
        raise DiffError("response reproduced the parent block unchanged")
    return rewrite, "rewrite"


def _apply_edits(block: str, edits: list[SearchReplace]) -> str:
    current = block
    for i, edit in enumerate(edits):
        needle = edit.search
        if needle.strip() == "":
            raise DiffError(f"SEARCH block {i} was empty")
        if needle in current:
            current = current.replace(needle, edit.replace, 1)
            continue
        # Tolerate trailing-whitespace drift, which models introduce constantly.
        loose = _find_loose(current, needle)
        if loose is None:
            preview = needle.strip().splitlines()[0][:80] if needle.strip() else ""
            raise DiffError(
                f"SEARCH block {i} did not match the parent block "
                f"(first line: {preview!r})"
            )
        start, stop = loose
        current = current[:start] + edit.replace + current[stop:]
    if current.strip() == block.strip():
        raise DiffError("edits applied but produced an identical block")
    return current


def _find_loose(haystack: str, needle: str) -> tuple[int, int] | None:
    """Locate `needle` in `haystack` ignoring per-line trailing whitespace."""
    hay_lines = haystack.splitlines(keepends=True)
    need_lines = [line.rstrip() for line in needle.rstrip("\n").splitlines()]
    if not need_lines:
        return None
    for i in range(len(hay_lines) - len(need_lines) + 1):
        window = [line.rstrip() for line in hay_lines[i : i + len(need_lines)]]
        if window == need_lines:
            start = sum(len(line) for line in hay_lines[:i])
            stop = start + sum(len(line) for line in hay_lines[i : i + len(need_lines)])
            return start, stop
    return None
