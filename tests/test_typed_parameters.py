"""`problem.parameters`: tuning an external command without writing any Python.

The user declares a typed space and a command line. Everything between the two
is the framework's job: the skeleton is generated, a candidate's configuration
is resolved and validated in the static stage -- before it can cost a second of
solver time -- and the values reach the command as flags or as a JSON file.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

from evolvekit.candidate import Candidate
from evolvekit.config import ConfigError, build_config
from evolvekit.evaluate import Cascade, build_argv
from evolvekit.prompts import system_prompt
from evolvekit.search.driver import Driver

PARAMETERS = {
    "neighbours": {"type": "int", "low": 10, "high": 120, "default": 50, "help": "arcs kept per client"},
    "penalty": {"type": "float", "low": 1e2, "high": 1e6, "default": 1e4, "log": True},
    "exhaustive": {"type": "bool", "default": True},
    "init": {"type": "choice", "choices": ["greedy", "savings"], "default": "greedy"},
}

# A stand-in for a solver written in something else: flags in, one JSON object
# on the path it was told to write. It knows nothing about evolvekit.
SOLVER = (
    "import argparse, json\n"
    "p = argparse.ArgumentParser()\n"
    "p.add_argument('--out'); p.add_argument('--neighbours', type=int)\n"
    "p.add_argument('--penalty', type=float); p.add_argument('--exhaustive')\n"
    "p.add_argument('--init'); p.add_argument('--config')\n"
    "a = p.parse_args()\n"
    "if a.config:\n"
    "    c = json.load(open(a.config))\n"
    "    a.neighbours, a.penalty, a.exhaustive, a.init = c['neighbours'], c['penalty'], str(c['exhaustive']).lower(), c['init']\n"
    "assert a.exhaustive in ('true', 'false'), a.exhaustive\n"
    "cost = 1000 + abs(a.neighbours - 30) * 5 + (0 if a.init == 'savings' else 40) + (25 if a.exhaustive == 'true' else 0)\n"
    "json.dump({'kpis': {'cost': cost, 'seen_neighbours': a.neighbours}}, open(a.out, 'w'))\n"
)


def _raw(tmp_path: Path, command: str | None = None, **problem) -> dict:
    (tmp_path / "solver.py").write_text(SOLVER, encoding="utf-8")
    return {
        "problem": {"parameters": copy.deepcopy(PARAMETERS), "description": "Tune the solver.", **problem},
        "evaluate": {
            "stages": [
                {"id": "static", "kind": "builtin-static"},
                {
                    "id": "full",
                    "kind": "command",
                    "command": command or f'"{sys.executable}" solver.py --out {{out}} {{params}}',
                    "timeout": 60,
                },
            ],
            "score": {"objective": "cost", "direction": "minimize"},
        },
        "search": {
            "operators": {"param_lhs": 1.0},
            "children_per_generation": 4,
            "generations": 3,
            "seed": 1,
            "novelty": {"behavioural": "off"},
        },
    }


# -- configuration ---------------------------------------------------------


def test_a_parameter_space_replaces_the_skeleton(tmp_path):
    config = build_config(_raw(tmp_path), base_dir=tmp_path)
    assert config.problem.skeleton is None and config.models is None
    assert config.problem.required_functions == ("configure",)
    source = config.problem.skeleton_source()
    assert source.count("# EVOLVE-BLOCK-START") == 1 and '"neighbours": 50' in source


def test_a_mistake_in_a_declaration_is_a_config_error_with_its_key_path(tmp_path):
    raw = _raw(tmp_path)
    raw["problem"]["parameters"]["neighbours"]["default"] = 500
    with pytest.raises(ConfigError, match=r"problem\.parameters\.neighbours\.default: 500 is above its maximum 120"):
        build_config(raw, base_dir=tmp_path)


def test_skeleton_and_parameters_are_alternatives(tmp_path):
    (tmp_path / "skeleton.py").write_text("# EVOLVE-BLOCK-START\nx = 1\n# EVOLVE-BLOCK-END\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="either `skeleton`"):
        build_config(_raw(tmp_path, skeleton="skeleton.py"), base_dir=tmp_path)


def test_a_command_that_never_receives_the_configuration_is_refused(tmp_path):
    with pytest.raises(ConfigError) as error:
        build_config(_raw(tmp_path, command="python solver.py --out {out}"), base_dir=tmp_path)
    assert "never receives the configuration" in str(error.value) and "{params}" in str(error.value)


def test_params_without_a_declared_space_is_refused(tmp_path, minimal_raw):
    minimal_raw["evaluate"]["stages"].append(
        {"id": "full", "kind": "command", "command": "solver {out} {params}"}
    )
    with pytest.raises(ConfigError, match="declares no parameters to substitute"):
        build_config(minimal_raw, base_dir=tmp_path)



@pytest.mark.parametrize("token", ["--opts={params}", "'--opts {params}'", "x{params}"])
def test_params_inside_a_larger_argument_is_refused(tmp_path, token):
    # `{params}` expands to several arguments. Inside a larger one it could
    # only be pasted in as one string with spaces -- `--opts=--neighbours 40
    # --init savings` -- which no option parser reads as the configuration.
    with pytest.raises(ConfigError) as error:
        build_config(_raw(tmp_path, command=f"solver --out {{out}} {token}"), base_dir=tmp_path)
    message = str(error.value)
    assert "evaluate.stages[1].command" in message and "{params} must be an argument of its own" in message
    assert "{params_json}" in message, "the way out, for a program that wants one argument"


def test_params_inside_a_larger_argument_is_a_bad_template_to_build_argv_too(tmp_path):
    with pytest.raises(ValueError, match="must be an argument of its own"):
        build_argv(
            "solver --opts={params}", candidate=tmp_path / "c.py", inputs=[], out=tmp_path / "o.json",
            params_flags=["--neighbours", "40"],
        )

# -- the command line ------------------------------------------------------


def test_params_expands_to_one_argument_per_flag_and_value(tmp_path):
    argv = build_argv(
        "solver --instance x.vrp {params} --out {out}",
        candidate=tmp_path / "c.py", inputs=[], out=tmp_path / "o.json",
        params_flags=["--neighbours", "40", "--init", "two words"],
    )
    assert argv == ["solver", "--instance", "x.vrp", "--neighbours", "40", "--init", "two words",
                    "--out", str(tmp_path / "o.json")]


def test_params_json_is_the_path_of_a_file_with_the_values(tmp_path):
    argv = build_argv(
        "solver --config {params_json}", candidate=tmp_path / "c.py", inputs=[],
        out=tmp_path / "o.json", params_json=tmp_path / "c.params.json",
    )
    assert argv == ["solver", "--config", str(tmp_path / "c.params.json")]


# -- resolved and validated before it costs anything -----------------------


def _candidate(config, block: str, cid: str = "g001-c0002") -> Candidate:
    skeleton = config.problem.skeleton_source()
    from evolvekit.candidate import splice_block

    source = splice_block(skeleton, block, config.problem.block_start, config.problem.block_end)
    return Candidate(id=cid, generation=1, block=block, source=source, operator="rewrite")


def test_the_configuration_reaches_the_solver_as_flags_and_is_recorded_as_data(tmp_path):
    config = build_config(_raw(tmp_path), base_dir=tmp_path)
    block = config.problem.parameters.render_block(
        {"neighbours": 30, "penalty": 5000.0, "exhaustive": False, "init": "savings"}
    )
    result = Cascade(config, work_dir=tmp_path / "work").evaluate_generation(
        [_candidate(config, block)]
    )["g001-c0002"]
    assert result.params == {"neighbours": 30, "penalty": 5000.0, "exhaustive": False, "init": "savings"}
    assert result.kpis["seen_neighbours"] == 30 and result.kpis["cost"] == 1000
    assert result.competes is True


def test_a_solver_that_reads_a_file_gets_a_file(tmp_path):
    raw = _raw(tmp_path, command=f'"{sys.executable}" solver.py --out {{out}} --config {{params_json}}')
    config = build_config(raw, base_dir=tmp_path)
    block = config.problem.parameters.render_block({**config.problem.parameters.defaults(), "neighbours": 31})
    result = Cascade(config, work_dir=tmp_path / "work").evaluate_generation(
        [_candidate(config, block)]
    )["g001-c0002"]
    assert result.kpis["seen_neighbours"] == 31
    written = json.loads((tmp_path / "work" / "candidates" / "g001-c0002.params.json").read_text(encoding="utf-8"))
    assert written["neighbours"] == 31 and written["exhaustive"] is True


@pytest.mark.parametrize(
    "body, fragment",
    [
        ('return {"neighbours": 500}', "neighbours: 500 is above its maximum 120"),
        ('return {"neighbors": 40}', "unknown parameter 'neighbors'"),
        ('return {"exhaustive": "yes"}', "expected true or false"),
        ("return [1, 2]", "must return a dict"),
        ("raise RuntimeError('no')", "RuntimeError: no"),
    ],
)
def test_an_invalid_configuration_is_rejected_before_the_solver_is_started(tmp_path, body, fragment):
    config = build_config(_raw(tmp_path), base_dir=tmp_path)
    block = f"def configure():\n    {body}\n"
    result = Cascade(config, work_dir=tmp_path / "work").evaluate_generation(
        [_candidate(config, block)]
    )["g001-c0002"]
    assert result.rejected is True and fragment in (result.reject_reason or "") + (result.last_failure or "")
    assert result.stages_reached == [], "the solver must never have been started"
    assert not list((tmp_path / "work").glob("stage_out/*"))


def test_a_configuration_a_model_computed_is_still_just_a_configuration(tmp_path):
    """An LLM operator may rewrite `configure()` into logic; what counts is what it returns."""
    config = build_config(_raw(tmp_path), base_dir=tmp_path)
    block = "def configure():\n    n = 10 * 3\n    return {'neighbours': n, 'init': 'savings' if n < 40 else 'greedy'}\n"
    result = Cascade(config, work_dir=tmp_path / "work").evaluate_generation(
        [_candidate(config, block)]
    )["g001-c0002"]
    assert result.params == {"neighbours": 30, "penalty": 10000.0, "exhaustive": True, "init": "savings"}


def test_the_prompt_tells_a_model_the_ranges_it_has_to_stay_inside(tmp_path):
    raw = _raw(tmp_path)
    model = {"provider": "fake", "model": "m", "options": {"responses": ["x"]}}
    raw["models"] = {"small": model, "strong": model}
    raw["search"]["operators"] = {"rewrite": 1.0}
    prompt = system_prompt(build_config(raw, base_dir=tmp_path), "", "")
    assert "`neighbours`: int in [10, 120]; default 50 -- arcs kept per client" in prompt
    assert "`penalty`: float in [100, 1e+06], log scale" in prompt
    assert "`init`: one of 'greedy', 'savings'" in prompt


# -- what the status document says about them ------------------------------


def _typed_run(tmp_path, configurations):
    """A run directory written by hand: the space in `run_started`, a
    configuration and a cost on each row."""
    from evolvekit.space import ParameterSpace
    from tests.test_status import RunDir

    space = ParameterSpace.parse(copy.deepcopy(PARAMETERS))
    run = RunDir(tmp_path)
    run.started(100, parameters=space.describe())
    run.row("g000-c0001", 0, 1200.0, params=space.defaults())
    for index, (values, cost) in enumerate(configurations, start=2):
        run.row(f"g001-c{index:04d}", 1, cost, params={**space.defaults(), **values}, block="rendered\n")
    return run


def test_status_reads_a_typed_configuration_from_the_row_not_from_its_code(tmp_path):
    from evolvekit.status import build_status, candidate_detail

    _typed_run(tmp_path, [({"neighbours": 30, "init": "savings", "exhaustive": False}, 1000.0)])
    best = build_status(tmp_path)["best"]
    by_name = {p["name"]: p for p in best["parameters"]}
    assert list(by_name) == list(PARAMETERS), "declaration order, as the user wrote it"
    assert by_name["neighbours"]["value"] == 30 and by_name["neighbours"]["changed"] is True
    assert by_name["init"]["value"] == "savings" and by_name["init"]["choices"] == ["greedy", "savings"]
    assert by_name["exhaustive"]["value"] is False and by_name["exhaustive"]["changed"] is True
    assert by_name["penalty"]["changed"] is False
    assert candidate_detail(tmp_path, "g001-c0002")["parameters"] == best["parameters"]


def test_a_log_scale_parameter_is_placed_by_its_logarithm_and_a_category_by_its_slot(tmp_path):
    from evolvekit.status import build_status

    _typed_run(tmp_path, [({"penalty": 1e5}, 1000.0)])
    by_name = {p["name"]: p for p in build_status(tmp_path)["best"]["parameters"]}
    assert by_name["penalty"]["default_position"] == pytest.approx(0.5), "1e4 is halfway from 1e2 to 1e6"
    assert by_name["penalty"]["value_position"] == pytest.approx(0.75)
    assert by_name["exhaustive"]["default_position"] == pytest.approx(0.75), "`true` is the second of two slots"
    assert by_name["init"]["default_position"] == pytest.approx(0.25)


def test_coverage_counts_a_category_per_value_and_a_log_range_per_decade(tmp_path):
    from evolvekit.status import build_status

    _typed_run(tmp_path, [
        ({"init": "savings", "penalty": 150.0}, 1000.0),
        ({"init": "savings", "penalty": 9e5}, 1010.0),
        ({"init": "greedy", "exhaustive": False}, 1100.0),
    ])
    items = {i["name"]: i for i in build_status(tmp_path)["parameters"]["items"]}
    assert items["init"]["coverage_labels"] == ["greedy", "savings"] and items["init"]["coverage"] == [2, 2]
    assert items["exhaustive"]["coverage_labels"] == ["false", "true"] and items["exhaustive"]["coverage"] == [1, 3]
    assert items["penalty"]["coverage"] == [1, 0, 0, 0, 0, 2, 0, 0, 0, 1] and items["penalty"]["log"] is True
    levels = {level["value"]: level for level in items["init"]["levels"]}
    assert levels["savings"]["n"] == 2 and levels["savings"]["mean_objective"] == pytest.approx(1005.0)


def test_a_switch_that_decides_the_score_ranks_above_a_number_that_does_not(tmp_path):
    from evolvekit.status import build_status

    # `init` decides the cost; `neighbours` wanders without effect.
    _typed_run(tmp_path, [
        ({"init": "savings", "neighbours": n}, 1000.0 + i) if i % 2 else ({"init": "greedy", "neighbours": n}, 1100.0 + i)
        for i, n in enumerate([20, 90, 35, 110, 60, 15, 80, 45])
    ])
    items = build_status(tmp_path)["parameters"]["items"]
    assert items[0]["name"] == "init" and items[0]["importance"]["value"] > 0.8
    assert items[0]["importance"]["sign"] == 1, "the score leans towards the second value, `savings`"
    neighbours = next(i for i in items if i["name"] == "neighbours")
    assert neighbours["importance"]["value"] < items[0]["importance"]["value"]


def test_a_many_way_choice_is_not_important_just_for_having_many_values():
    from evolvekit.status import _correlation_ratio

    # Six categories over twelve scores that have nothing to do with them.
    groups = ["a", "b", "c", "d", "e", "f"] * 2
    scores = [5.0, 1.0, 9.0, 3.0, 11.0, 7.0, 6.0, 12.0, 2.0, 10.0, 4.0, 8.0]
    assert _correlation_ratio(groups, scores) == 0.0
    # ...and one whose categories are the whole story is.
    assert _correlation_ratio(["a"] * 4 + ["b"] * 4 + ["c"] * 4, sorted(scores)) > 0.9


# -- the whole loop --------------------------------------------------------


@pytest.mark.slow
def test_a_whole_run_tunes_a_foreign_solver_with_no_model_and_no_python_written(tmp_path):
    config = build_config(_raw(tmp_path), base_dir=tmp_path)
    driver = Driver(config, run_dir=tmp_path / "run")
    summary = driver.run()

    rows = driver.ledger.runs()
    seed = rows[0]
    assert seed["params"] == {"neighbours": 50, "penalty": 10000.0, "exhaustive": True, "init": "greedy"}
    assert summary.seed_score == pytest.approx(-(1000 + 100 + 40 + 25))
    assert summary.best.score > summary.seed_score, "twelve samples of this space beat its default"
    assert summary.totals.get("calls", 0.0) == 0.0

    children = [r for r in rows[1:] if not r["rejected"]]
    assert children and all(r["params"] and r["operator"] == "param_lhs" for r in children)
    assert {r["params"]["init"] for r in children} == {"greedy", "savings"}, "a choice is swept, not frozen"
    assert {r["params"]["exhaustive"] for r in children} == {True, False}, "so is a boolean"

    from evolvekit.events import read_events

    started = read_events(tmp_path / "run")[0]
    assert [p["name"] for p in started["parameters"]] == list(PARAMETERS)


# -- preflight -------------------------------------------------------------


@pytest.mark.parametrize("placeholder", ["{params}", "--config {params_json}"])
def test_preflight_runs_the_command_with_the_default_configuration(tmp_path, placeholder):
    """The seed *is* the declared defaults, so that is what `preflight` has to
    hand the command -- not an empty `{params}`, which measures whatever the
    program does when it is told nothing."""
    from evolvekit.preflight import preflight

    raw = _raw(tmp_path, command=f'"{sys.executable}" solver.py --out {{out}} {placeholder}')
    raw["problem"]["parameters"]["neighbours"]["default"] = 31
    report = preflight(build_config(raw, base_dir=tmp_path))
    assert report.failures == []
    assert report.stages[-1].kpis["seen_neighbours"] == 31
