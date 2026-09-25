"""A solver that was not written for evolvekit reports the way it always has:
a JSON object of its own on stdout, or a line of text. The framework reads
that, instead of asking for a wrapper script whose only job is to re-spell it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from evolvekit.config import ConfigError, StageConfig, build_config
from evolvekit.evaluate.stages import _read_kpis, run_command_stage


def _stage(tmp_path: Path, program: str, **keys) -> StageConfig:
    (tmp_path / "solver.py").write_text(program, encoding="utf-8")
    command = keys.pop("command", f'"{sys.executable}" solver.py {{candidate}}')
    return StageConfig.parse({"id": "full", "kind": "command", "command": command, "timeout": 60, **keys}, 1)


def _run(tmp_path: Path, stage: StageConfig, **kwargs):
    candidate = tmp_path / "candidate.py"
    candidate.write_text("", encoding="utf-8")
    return run_command_stage(
        candidate, stage, inputs=(), out_path=tmp_path / "out" / "c.full.json", cwd=tmp_path, **kwargs
    )


# -- a JSON object that is the solver's own --------------------------------


def test_a_flat_result_keeps_its_numbers_and_leaves_its_metadata_alone(tmp_path):
    out = tmp_path / "result.json"
    out.write_text(json.dumps({
        "cost": 9668672, "feasible": True, "iterations": 1253, "instance": "s01", "seed": 1,
        "params": {"num_neighbours": 50}, "operators": ["Relocate1", "Swap11"],
        "convergence": [None, 9719392, 9668672], "excess_load": [0, 0], "best_known": None,
    }), encoding="utf-8")
    kpis, vectors, note, problem = _read_kpis(out)
    assert problem is None and note == ""
    assert kpis == {"cost": 9668672.0, "feasible": 1.0, "iterations": 1253.0, "seed": 1.0}
    assert vectors == {"excess_load": [0.0, 0.0]}, "a list with a hole in it is not a vector KPI"


def test_under_an_explicit_kpis_key_a_string_is_still_a_mistake(tmp_path):
    out = tmp_path / "result.json"
    out.write_text(json.dumps({"kpis": {"cost": 1.0, "status": "optimal"}}), encoding="utf-8")
    *_, problem = _read_kpis(out)
    assert problem is not None and "'status'" in problem


def test_a_flat_result_with_no_number_in_it_is_a_failure(tmp_path):
    out = tmp_path / "result.json"
    out.write_text(json.dumps({"status": "optimal", "instance": "s01"}), encoding="utf-8")
    *_, problem = _read_kpis(out)
    assert problem == "evaluator output contained no scalar KPI"


def test_not_a_number_is_a_failure_in_either_shape(tmp_path):
    out = tmp_path / "result.json"
    out.write_text('{"cost": NaN, "instance": "s01"}', encoding="utf-8")
    *_, problem = _read_kpis(out)
    assert problem is not None and "cost" in problem and "finite" in problem


# -- the last line of stdout -----------------------------------------------


PRINTS_JSON = (
    "import json, sys\n"
    "print('reading instance ...')\n"
    "print(json.dumps({'progress': 0.5}))\n"
    "sys.stderr.write('warning: something\\n')\n"
    "print(json.dumps({'cost': 1234.5, 'feasible': True, 'instance': 'a'}))\n"
    "print()\n"
)


def test_the_result_is_the_last_json_object_the_program_printed(tmp_path):
    stage = _stage(tmp_path, PRINTS_JSON, kpis_from="stdout")
    outcome = _run(tmp_path, stage, required_kpis=("cost",))
    assert outcome.ok and outcome.kpis == {"cost": 1234.5, "feasible": 1.0}


def test_a_command_that_reports_on_stdout_needs_no_out_placeholder(tmp_path):
    with pytest.raises(ConfigError) as error:
        _stage(tmp_path, PRINTS_JSON)
    message = str(error.value)
    assert "{out}" in message and "kpis_from: stdout" in message and "kpi_patterns" in message
    assert _stage(tmp_path, PRINTS_JSON, kpis_from="stdout").kpis_from == "stdout"


def test_a_program_that_printed_no_result_says_so_with_what_it_did_print(tmp_path):
    stage = _stage(tmp_path, "print('Segmentation fault (core dumped)')\n", kpis_from="stdout")
    outcome = _run(tmp_path, stage)
    assert not outcome.ok
    assert outcome.failure == "the program printed no JSON object on stdout"
    assert "Segmentation fault" in outcome.stdout


# -- a line of text --------------------------------------------------------


PRINTS_TEXT = (
    "print('iter 100  best cost: 1300.0')\n"
    "print('iter 200  best cost: 1250.5')\n"
    "print('Solved in 12.75 s, 3 vehicles')\n"
)
PATTERNS = {"cost": r"best cost: ([-+0-9.eE]+)", "runtime_s": r"Solved in ([0-9.]+) s", "vehicles": r"(\d+) vehicles"}


def test_numbers_are_picked_out_of_text_and_the_last_match_is_the_result(tmp_path):
    stage = _stage(tmp_path, PRINTS_TEXT, kpi_patterns=PATTERNS)
    outcome = _run(tmp_path, stage, required_kpis=("cost",))
    assert outcome.ok and outcome.kpis == {"cost": 1250.5, "runtime_s": 12.75, "vehicles": 3.0}


def test_a_pattern_that_matched_nothing_names_itself(tmp_path):
    stage = _stage(tmp_path, "print('no solution found')\n", kpi_patterns=PATTERNS)
    outcome = _run(tmp_path, stage)
    assert not outcome.ok
    assert outcome.failure == "`kpi_patterns.cost` matched nothing in what the program printed"
    assert "no solution found" in outcome.stdout


def test_patterns_add_to_a_json_result(tmp_path):
    program = "import json\nprint('peak memory: 512 MB')\nprint(json.dumps({'cost': 7.0}))\n"
    stage = _stage(tmp_path, program, kpis_from="stdout", kpi_patterns={"peak_mb": r"peak memory: (\d+) MB"})
    assert _run(tmp_path, stage).kpis == {"cost": 7.0, "peak_mb": 512.0}


@pytest.mark.parametrize(
    "patterns, fragment",
    [
        ({"cost": "cost: ("}, "kpi_patterns.cost: not a valid regular expression"),
        ({"cost": "cost: [0-9]+"}, "kpi_patterns.cost: needs exactly one capturing group"),
        ({"cost": r"(\w+): (\d+)"}, "kpi_patterns.cost: needs exactly one capturing group"),
        ({}, "kpi_patterns: expected a non-empty mapping"),
    ],
)
def test_a_pattern_that_cannot_work_is_a_config_error(tmp_path, patterns, fragment):
    with pytest.raises(ConfigError) as error:
        _stage(tmp_path, PRINTS_TEXT, kpi_patterns=patterns)
    assert fragment in str(error.value)


def test_kpis_from_has_two_values(tmp_path):
    with pytest.raises(ConfigError, match=r"kpis_from: must be one of \['file', 'stdout'\]"):
        _stage(tmp_path, PRINTS_JSON, kpis_from="stderr")


# -- the whole path: flags in, stdout out, once per instance ---------------


def test_a_foreign_solver_is_tuned_with_nothing_written_for_it(tmp_path):
    from evolvekit.candidate import SEED_OPERATOR, Candidate, splice_block
    from evolvekit.evaluate import Cascade

    (tmp_path / "solver.py").write_text(
        "import argparse, json\n"
        "p = argparse.ArgumentParser()\n"
        "p.add_argument('--instance'); p.add_argument('--neighbours', type=int)\n"
        "a = p.parse_args()\n"
        "print('solving', a.instance)\n"
        "print(json.dumps({'objective': 1000 + a.neighbours, 'feasible': True, 'instance': a.instance}))\n",
        encoding="utf-8",
    )
    config = build_config(
        {
            "problem": {"parameters": {"neighbours": {"type": "int", "low": 10, "high": 120, "default": 50}}},
            "evaluate": {
                "stages": [
                    {"id": "static", "kind": "builtin-static"},
                    {
                        "id": "full", "kind": "command", "kpis_from": "stdout", "instances": ["a", "b"],
                        "command": f'"{sys.executable}" solver.py --instance {{instance}} {{params}}',
                    },
                ],
                "score": {"objective": "objective", "direction": "minimize"},
            },
            "search": {"operators": {"param_lhs": 1.0}},
        },
        base_dir=tmp_path,
    )
    space = config.problem.parameters
    block = space.render_block(space.defaults())
    source = splice_block(config.problem.skeleton_source(), block, config.problem.block_start, config.problem.block_end)
    seed = Candidate(id="g000-c0001", generation=0, block=block, source=source, operator=SEED_OPERATOR)
    result = Cascade(config, work_dir=tmp_path / "work").evaluate_generation([seed])[seed.id]
    assert result.competes and result.kpis["objective"] == pytest.approx(100.0)
    assert result.kpis["objective_raw"] == 1050.0 and result.kpis["feasible"] == 1.0


def test_a_pattern_that_captured_something_else_than_a_number_says_what(tmp_path):
    stage = _stage(tmp_path, "print('status: optimal')\n", kpi_patterns={"cost": r"status: (\w+)"})
    outcome = _run(tmp_path, stage)
    assert not outcome.ok
    assert outcome.failure == "`kpi_patterns.cost` captured 'optimal', which is not a number"


def test_a_pattern_anchored_at_the_start_of_a_line_matches_every_line(tmp_path):
    # `^Cost:` is how anybody writes "the line that reports the cost". Without
    # re.MULTILINE `^` means the start of the whole output, and the run failed
    # with "matched nothing" although the line was right there.
    program = "print('reading instance')\nprint('Cost: 1300')\nprint('Cost: 1250')\nprint('done')\n"
    stage = _stage(tmp_path, program, kpi_patterns={"cost": r"^Cost: (\d+)$"})
    outcome = _run(tmp_path, stage)
    assert outcome.ok, outcome.failure
    assert outcome.kpis == {"cost": 1250.0}


def test_a_result_line_longer_than_the_log_tail_is_still_found(tmp_path):
    # A solver that prints its whole solution with its cost -- one JSON line of
    # a few hundred kilobytes -- was reported as having printed no JSON object,
    # because only the last 64 KB of the log was looked at and that is the
    # second half of a line.
    program = (
        "import json\n"
        "print('solving ...')\n"
        "print(json.dumps({'cost': 7.0, 'solution': 'r' * 200_000}))\n"
        "print('done')\n"
    )
    stage = _stage(tmp_path, program, kpis_from="stdout")
    outcome = _run(tmp_path, stage, required_kpis=("cost",))
    assert outcome.ok, outcome.failure
    assert outcome.kpis == {"cost": 7.0}


def test_the_last_json_object_is_found_after_megabytes_of_other_output(tmp_path):
    program = (
        "import json\n"
        "print(json.dumps({'cost': 1.0}))\n"
        "for i in range(60_000):\n"
        "    print('iteration', i, 'x' * 40)\n"
        "print(json.dumps({'cost': 2.0}))\n"
        "for i in range(3_000):\n"
        "    print('cleanup', i, 'y' * 40)\n"
    )
    stage = _stage(tmp_path, program, kpis_from="stdout")
    assert _run(tmp_path, stage).kpis == {"cost": 2.0}
