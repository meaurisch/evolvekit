"""The LLM operators' prompts on a tree problem (design §5.3).

A skeleton's prompt shows its one evolve block. A tree's shows the open files
as they are after the parent's diff, each line numbered, and asks for one
unified diff of them; `parse_diff_response` finds that diff in the reply.

Marked blocks are the cheap special case: when the open files hold exactly one
`# EVOLVE-BLOCK-START` / `# EVOLVE-BLOCK-END` pair, the prompt shows only that
block and asks for it in the skeleton's formats (SEARCH/REPLACE, or the whole
block); the operator splices the answer back and the tree's diff is the child.

Files are read as bytes and decoded with `surrogateescape`, so a byte that is
not UTF-8 reaches the prompt as a lone surrogate and goes back to disk as the
byte it was. Only imported for a tree problem (it imports `evolvekit.tree`).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from evolvekit.candidate import BlockError, Candidate, extract_block
from evolvekit.config import Config
from evolvekit.diffs import DiffError
from evolvekit.evaluate.types import EvalResult
from evolvekit.prompts import (
    _DIFF_FORMAT,
    _REWRITE_FORMAT,
    FAILURE_LIMIT,
    FEEDBACK_LIMIT,
    FEEDBACK_LINES,
    OPERATOR_INSTRUCTIONS,
    Inspiration,
    _feedback_section,
    _scratchpad_section,
    _score_breakdown,
    problem_sections,
)
from evolvekit.tree import Tree, git, glob_pattern

__all__ = [
    "DIFF_FORMAT",
    "INSPIRATION_LINES",
    "REFUSALS_SHOWN",
    "Refusal",
    "marked_block",
    "numbered",
    "open_files",
    "parse_diff_response",
    "tree_messages",
]

TREE_PROTOCOL = """\
You improve a program by changing its source files. Only the files under "Open files" may change; \
every other file is fixed.

Hard rules:
- Reply with ONE unified diff in a ```diff fence and nothing else.
- Paths are relative to the folder the open files are listed from: --- a/solver.py, +++ b/solver.py.
- Keep the program's command line, its output and its tests working.
- No explanation outside the diff.
"""

BLOCK_PROTOCOL = """\
You improve a program by changing one marked region of one of its source files. The rest of that \
file and every other file are fixed, and you must not restate them.

Hard rules:
- Return ONLY the new contents of the marked block, in the requested format.
- Keep every function the block already defines, with the same names and signatures; the rest of \
the program calls them.
- Keep the program's command line, its output and its tests working.
- No explanation outside the code or diff blocks. No commentary, no preamble.
"""

DIFF_FORMAT = (
    "Reply with a unified diff of the open files inside a single ```diff fence and nothing else. "
    "Hunk headers (@@ -12,4 +12,5 @@) must match the numbered lines above; give three lines of "
    "context around every change."
)

INSPIRATION_LINES = 80
"""An inspiration's diff is shown up to this many lines: the prompt stays bounded."""

REFUSALS_SHOWN = 3
"""The most recent refused changes (build and static-check failures) a prompt shows."""


@dataclass(frozen=True)
class Refusal:
    """A recent candidate the build or the static check refused: never a parent,
    so its reason reaches the model only through this section."""

    id: str
    reason: str
    tail: str = ""


def open_files(made: Tree, open: Sequence[str]) -> dict[str, str]:
    """The files of `made` that `open` (globs relative to the repo's folder) names,
    as `{tree-relative POSIX path: text}`, sorted. A binary file (a NUL byte) is left out."""
    listed = [name for name in git(made.path, "ls-tree", "-r", "-z", "--name-only", "HEAD", "--", ".")
              .split("\0") if name]
    patterns = [glob_pattern(p) for p in open]
    files: dict[str, str] = {}
    for name in sorted(listed):
        if not any(p.fullmatch(name) for p in patterns):
            continue
        path = made.path / name
        if not path.is_file():
            continue
        data = path.read_bytes()
        if b"\0" in data:
            continue
        files[name] = data.decode("utf-8", errors="surrogateescape")
    return files


def numbered(text: str) -> str:
    """Each line with its number (from 1), as a hunk header counts them."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return "\n".join(f"{n:>5}  {line.removesuffix(chr(13))}" for n, line in enumerate(lines, 1))


def marked_block(files: dict[str, str], start: str, end: str) -> tuple[str, str] | None:
    """`(path, block)` when the open files hold exactly one marker pair, else `None`."""
    holders = [(path, text) for path, text in files.items()
               if any(line.strip() in (start, end) for line in text.splitlines())]
    if len(holders) != 1:
        return None
    path, text = holders[0]
    try:
        _, block, _ = extract_block(text, start, end)
    except BlockError:
        return None
    return path, block


def _fence(text: str, lang: str = "") -> str:
    """`text` in a code fence longer than any backtick run inside it."""
    longest = max((len(run) for run in re.findall(r"`{3,}", text)), default=0)
    marks = "`" * max(3, longest + 1)
    return f"{marks}{lang}\n{text.rstrip(chr(10))}\n{marks}"


def _tail(text: str) -> str:
    lines = text.strip("\n").splitlines()[-FEEDBACK_LINES:]
    return "\n".join(lines)[-FEEDBACK_LIMIT:]


def _refusal_section(refusals: Sequence[Refusal]) -> str:
    shown = list(refusals)[-REFUSALS_SHOWN:]
    if not shown:
        return ""
    parts = ["\n## Recent changes that were refused (none of them is in the files above)"]
    for item in shown:
        parts.append(f"### {item.id}: {item.reason}")
        if item.tail.strip():
            parts.append(_fence(_tail(item.tail)))
    return "\n".join(parts) + "\n"


def _inspiration_section(inspirations: Sequence[Inspiration]) -> str:
    if not inspirations:
        return ""
    parts = ["\n## Other combinations in the archive (do not copy them verbatim)"]
    for item in inspirations:
        parts.append(f"### Another combination's change ({item.id}, {item.score_line})")
        if item.block is not None and item.block.strip():
            lines = item.block.rstrip("\n").split("\n")
            shown = "\n".join(lines[:INSPIRATION_LINES])
            if len(lines) > INSPIRATION_LINES:
                shown += f"\n... ({len(lines) - INSPIRATION_LINES} more lines)"
            parts.append(_fence(shown, "diff"))
        else:
            parts.append(item.summary)
    return "\n".join(parts) + "\n"


def tree_messages(
    config: Config,
    *,
    parent: Candidate,
    result: EvalResult | None,
    operator: str,
    files: dict[str, str],
    best_score: float | None = None,
    inspirations: Sequence[Inspiration] = (),
    scratchpad: str | None = None,
    extra_instruction: str | None = None,
    block: tuple[str, str] | None = None,
    refusals: Sequence[Refusal] = (),
) -> list[dict[str, str]]:
    """The system message (the protocol, then the problem's sections: identical
    for every call of a run) and the bounded user message."""
    protocol = TREE_PROTOCOL if block is None else BLOCK_PROTOCOL
    system = f"{protocol}\n{problem_sections(config)}"

    if block is None:
        shown = "## Open files (as they are now)\n" + "".join(
            f"\n### {path}\n{_fence(numbered(text))}\n" for path, text in files.items())
        fmt = DIFF_FORMAT
    else:
        path, text = block
        shown = f"## Current block (in {path})\n{_fence(text, 'python')}\n"
        fmt = _DIFF_FORMAT if operator == "diff" else _REWRITE_FORMAT

    instruction = OPERATOR_INSTRUCTIONS.get(operator, OPERATOR_INSTRUCTIONS["diff"])
    if extra_instruction:
        instruction = f"{instruction}\n\n{extra_instruction.strip()}"
    failure = (result.last_failure if result is not None else parent.last_failure) or ""
    failure_section = (f"\n## Last failure artefact (truncated)\n{_fence(failure[-FAILURE_LIMIT:])}\n"
                       if failure.strip() else "")
    context = ""
    current = result.score if result is not None else parent.score
    if best_score is not None and current is not None:
        context = (f"\nBest score in the archive so far: {best_score:.6g} "
                   f"(this parent is {current - best_score:+.6g} against it).\n")
    user = (
        f"{shown}\n"
        f"## How it scored\n{_score_breakdown(result, parent)}\n"
        f"{_feedback_section(result, parent)}{context}{_refusal_section(refusals)}"
        f"{_inspiration_section(inspirations)}{_scratchpad_section(scratchpad)}{failure_section}\n"
        f"## Your task\n{instruction}\n\n{fmt}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


_FENCED = re.compile(r"^```[ \t]*([A-Za-z0-9_+-]*)[^\n]*\n(.*?)^```", re.DOTALL | re.MULTILINE)
_START = re.compile(r"^(?:diff --git |--- )", re.MULTILINE)


def _is_diff(text: str) -> bool:
    return all(re.search(mark, text, re.MULTILINE) for mark in (r"^@@ ", r"^--- ", r"^\+\+\+ "))


def parse_diff_response(text: str) -> str:
    """The unified diff in a reply: the first ```diff, ```patch or bare fence that
    holds one, else the text from its first `diff --git ` or `--- ` line (up to a
    closing fence). Raises `DiffError` when there is none."""
    for match in _FENCED.finditer(text):
        if match.group(1).lower() in ("diff", "patch", "") and _is_diff(match.group(2)):
            return match.group(2)
    start = _START.search(text)
    if start is not None:
        body = text[start.start():]
        close = re.search(r"^```", body, re.MULTILINE)
        if close is not None:
            body = body[:close.start()]
        if _is_diff(body):
            return body
    raise DiffError("the reply holds no unified diff (no '--- a/…', '+++ b/…' and '@@' lines)")
