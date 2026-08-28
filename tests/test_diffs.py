"""SEARCH/REPLACE application, and the full-rewrite fallback behind it."""

from __future__ import annotations

import pytest

from evolvekit.candidate import (
    BlockError,
    block_hash,
    complexity_of,
    extract_block,
    splice_block,
)
from evolvekit.diffs import DiffError, apply_response, parse_search_replace

BLOCK = 'def priority(item, bins):\n    """Best fit."""\n    return [-(b - item) for b in bins]\n'

START, END = "# EVOLVE-BLOCK-START", "# EVOLVE-BLOCK-END"


def sr(search: str, replace: str) -> str:
    return f"<<<<<<< SEARCH\n{search}\n=======\n{replace}\n>>>>>>> REPLACE"


# -- parsing ---------------------------------------------------------------


def test_parses_multiple_blocks_in_order():
    response = sr("a", "b") + "\n\n" + sr("c", "d")
    edits = parse_search_replace(response)
    # The captured text keeps its trailing newline: SEARCH must match exactly.
    assert [(e.search, e.replace) for e in edits] == [
        ("a\n", "b\n"),
        ("c\n", "d\n"),
    ]


def test_no_blocks_in_prose():
    assert parse_search_replace("I would change the constant.") == []


# -- applying --------------------------------------------------------------


def test_a_matching_edit_applies_as_a_diff():
    response = sr(
        "    return [-(b - item) for b in bins]", "    return [-(b - item) ** 2 for b in bins]"
    )
    new_block, mode = apply_response(BLOCK, response)
    assert mode == "diff"
    assert "** 2" in new_block
    assert '"""Best fit."""' in new_block  # the rest of the block survives


def test_several_edits_apply_in_sequence():
    response = sr('    """Best fit."""', '    """Tighter."""') + "\n" + sr(
        "    return [-(b - item) for b in bins]", "    return [0.0 for _ in bins]"
    )
    new_block, mode = apply_response(BLOCK, response)
    assert mode == "diff"
    assert "Tighter" in new_block and "0.0" in new_block


def test_trailing_whitespace_drift_still_matches():
    response = sr("    return [-(b - item) for b in bins]   ", "    return []")
    new_block, mode = apply_response(BLOCK, response)
    assert mode == "diff" and "return []" in new_block


def test_a_non_matching_edit_falls_back_to_the_fenced_rewrite():
    rewrite = "def priority(item, bins):\n    return [1.0 for _ in bins]\n"
    response = sr("this text is not in the block", "x") + f"\n\n```python\n{rewrite}```"
    new_block, mode = apply_response(BLOCK, response)
    assert mode == "rewrite"
    assert new_block.strip() == rewrite.strip()


def test_a_non_matching_edit_with_no_fallback_raises():
    response = sr("absent", "x")
    with pytest.raises(DiffError, match="did not match"):
        apply_response(BLOCK, response)


def test_a_bare_fenced_block_is_a_rewrite():
    rewrite = "def priority(item, bins):\n    return [2.0 for _ in bins]\n"
    new_block, mode = apply_response(BLOCK, f"Here you go:\n```python\n{rewrite}```")
    assert mode == "rewrite" and new_block.strip() == rewrite.strip()


def test_the_largest_fence_wins_when_several_are_present():
    small = "x = 1\n"
    large = "def priority(item, bins):\n    return [3.0 for _ in bins]\n"
    response = f"```\n{small}```\nand\n```python\n{large}```"
    new_block, _ = apply_response(BLOCK, response)
    assert new_block.strip() == large.strip()


def test_an_unfenced_code_response_is_accepted():
    rewrite = "def priority(item, bins):\n    return [4.0 for _ in bins]"
    new_block, mode = apply_response(BLOCK, rewrite)
    assert mode == "rewrite" and new_block.strip() == rewrite.strip()


def test_prose_only_is_an_error():
    with pytest.raises(DiffError, match="neither a SEARCH/REPLACE block nor a code fence"):
        apply_response(BLOCK, "I would need to see the arrival distribution first.")


def test_an_unchanged_block_is_an_error():
    with pytest.raises(DiffError, match="unchanged"):
        apply_response(BLOCK, f"```python\n{BLOCK}```")


def test_an_edit_that_changes_nothing_is_an_error():
    line = "    return [-(b - item) for b in bins]"
    with pytest.raises(DiffError, match="identical"):
        apply_response(BLOCK, sr(line, line))


def test_an_empty_search_block_is_an_error():
    with pytest.raises(DiffError, match="empty"):
        apply_response(BLOCK, sr("", "x"))


# -- the evolve block itself ----------------------------------------------


def test_extract_and_splice_round_trip(skeleton_source):
    prefix, block, suffix = extract_block(skeleton_source, START, END)
    assert "def priority(item, bins):" in block
    assert START in prefix and END in suffix
    assert prefix + block + suffix == skeleton_source
    spliced = splice_block(skeleton_source, "def priority(item, bins):\n    return []\n", START, END)
    assert "return []" in spliced
    assert extract_block(spliced, START, END)[0] == prefix


def test_missing_markers_are_reported():
    with pytest.raises(BlockError, match="expected exactly one"):
        extract_block("def f():\n    pass\n", START, END)


def test_duplicate_markers_are_reported():
    source = f"{START}\nx\n{END}\n{START}\ny\n{END}\n"
    with pytest.raises(BlockError, match="found 2 and 2"):
        extract_block(source, START, END)


def test_reversed_markers_are_reported():
    with pytest.raises(BlockError, match="appears before"):
        extract_block(f"{END}\nx\n{START}\n", START, END)


def test_complexity_counts_ast_nodes():
    assert complexity_of("x = 1\n") > 0
    assert complexity_of("def f(:\n") == -1


def test_block_hash_ignores_blank_lines_and_trailing_space():
    assert block_hash("a = 1\n\nb = 2  \n") == block_hash("a = 1\nb = 2\n")
    assert block_hash("a = 1\n") != block_hash("a = 2\n")
