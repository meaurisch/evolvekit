"""`problem.tools` / `constraints` / `what_counts_as_new` and how they render.

The second real Phase C circle-packing run is this file's reason to exist:
fifteen candidates, every one of them a hand-placed arrangement, against a
skeleton that had offered `deadline()`, `expired()` and `TIME_BUDGET_S` the
whole time. Nothing in the prompt named them where a model would land. These
tests assert the naming happens, and that it happens in the *cacheable* half.
"""

from __future__ import annotations

import pytest

from evolvekit.config import ConfigError, build_config, load_config
from evolvekit.prompts import system_prompt


def _config(minimal_raw, tmp_path, **problem):
    minimal_raw["problem"].update(problem)
    return build_config(minimal_raw, base_dir=tmp_path)


# -- parsing ---------------------------------------------------------------


def test_the_three_fields_default_to_empty(minimal_raw, tmp_path):
    problem = _config(minimal_raw, tmp_path).problem
    assert problem.tools == ()
    assert problem.constraints == ()
    assert problem.what_counts_as_new == ""


def test_bullets_parse_from_a_yaml_list_and_collapse_whitespace(minimal_raw, tmp_path):
    config = _config(
        minimal_raw,
        tmp_path,
        tools=["repair(c, r)\n  the referee", "sum_radii(r)"],
    )
    assert config.problem.tools == ("repair(c, r) the referee", "sum_radii(r)")


def test_bullets_also_parse_from_a_block_string(minimal_raw, tmp_path):
    """A `|` block is a reasonable thing to type; it must not be an error."""
    config = _config(minimal_raw, tmp_path, constraints="- no numpy\n- no I/O\n")
    assert config.problem.constraints == ("no numpy", "no I/O")


def test_a_non_string_bullet_names_its_index(minimal_raw, tmp_path):
    with pytest.raises(ConfigError, match=r"problem.tools\[1\]"):
        _config(minimal_raw, tmp_path, tools=["fine", 7])


def test_an_unknown_problem_key_is_still_rejected(minimal_raw, tmp_path):
    with pytest.raises(ConfigError, match="unknown key"):
        _config(minimal_raw, tmp_path, toolz=["typo"])


# -- rendering -------------------------------------------------------------


def test_sections_are_omitted_entirely_when_unset(minimal_raw, tmp_path):
    prompt = system_prompt(_config(minimal_raw, tmp_path), "PRE", "POST")
    assert "## Tools" not in prompt
    assert "## Constraints" not in prompt
    assert "## What counts as a new candidate" not in prompt


def test_each_field_becomes_its_own_headed_section(minimal_raw, tmp_path):
    config = _config(
        minimal_raw,
        tmp_path,
        tools=["deadline(seconds) and expired(when)"],
        constraints=["standard library only"],
        what_counts_as_new="A different arrangement, not a different spelling.",
    )
    prompt = system_prompt(config, "PRE", "POST")
    assert "## Tools your block may call and rely on" in prompt
    assert "- deadline(seconds) and expired(when)" in prompt
    assert "## Constraints\n- standard library only" in prompt
    assert "## What counts as a new candidate here" in prompt
    assert "A different arrangement, not a different spelling." in prompt
    # Order matters: the problem, then what it may use, then what it may not,
    # then the scoring. All of it before the skeleton dump.
    assert (
        prompt.index("## Problem")
        < prompt.index("## Tools")
        < prompt.index("## Constraints")
        < prompt.index("## What counts as a new")
        < prompt.index("## Scoring")
        < prompt.index("## The fixed skeleton")
    )


def test_the_sections_do_not_move_between_calls(minimal_raw, tmp_path):
    """They come from the config, so the cacheable prefix stays byte-identical."""
    config = _config(minimal_raw, tmp_path, tools=["a"], constraints=["b"])
    first = system_prompt(config, "PRE", "POST")
    second = system_prompt(config, "PRE", "POST")
    assert first == second


# -- the two examples ------------------------------------------------------


def test_circle_packing_names_the_time_budget_and_the_helpers(example_root):
    """The specific failure: the model never learned the local search existed."""
    config = load_config(example_root / "circlepacking" / "evolvekit.yaml")
    prompt = system_prompt(config, "PRE", "POST")
    for needle in ("TIME_BUDGET_S", "deadline(", "expired(", "repair(", "sum_radii("):
        assert needle in prompt, needle
    assert "while not expired(until):" in prompt, "no sketch of the search loop"
    assert "N_CIRCLES" in prompt


def test_bin_packing_says_what_a_monotone_re_expression_costs(example_root):
    config = load_config(example_root / "binpacking" / "evolvekit.yaml")
    prompt = system_prompt(config, "PRE", "POST")
    assert "## What counts as a new candidate here" in prompt
    assert "argmax" in prompt
    assert "priority_error" in prompt
