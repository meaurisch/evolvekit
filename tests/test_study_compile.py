"""A study compiled into an ordinary evolvekit config and the runner's
`study-run.json` (`evolvekit/harness/compile.py`)."""

from __future__ import annotations

import json

import pytest
import yaml

from evolvekit.config import build_config
from evolvekit.harness import HarnessError
from evolvekit.harness.compile import OPERATORS, OPERATORS_WITH_AI, compile_study, write_run
from evolvekit.harness.manifest import load_harness
from evolvekit.harness.plan import Plan, make_plan
from evolvekit.harness.study import Study
from harness_toy import write_toy_harness
from test_study import EXAMPLE, _study_folder, _with


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    return load_harness(write_toy_harness(tmp_path_factory.mktemp("h") / "toy"))


PLAN = Plan.fixed(children=6, generations=9, workers=3, pin=(2, 4, 6))


def _compile(tmp_path, harness, document=EXAMPLE, plan=PLAN, **kwargs):
    root = _study_folder(tmp_path, document)
    return compile_study(Study.parse(document), harness, plan, root=root, **kwargs)


def test_the_compiled_config_is_an_ordinary_evolvekit_config(tmp_path, harness):
    config, _ = _compile(tmp_path, harness)
    run_dir = tmp_path / "runs" / "r1"
    run_dir.mkdir(parents=True)
    loaded = build_config(config, base_dir=run_dir)
    assert loaded.problem.parameters.names == ["threshold", "factor", "a_values", "b_boost"]
    assert [c.text for c in loaded.problem.parameters.constraints] == ["a_values <= 2 * factor"]
    assert loaded.evaluate.score.levels[0].kpi == "picked_count"
    assert loaded.evaluate.gates[0].kpi == "guardrail_violation"
    assert loaded.budget.max_hours == pytest.approx(PLAN.max_hours, rel=1e-3)


def test_what_may_change_becomes_the_parameters(tmp_path, harness):
    config, _ = _compile(tmp_path, harness)
    parameters = config["problem"]["parameters"]
    # A tuned setting: the harness's range, starting at the input settings
    # file's value (4.0), explained the way the harness explains it.
    assert parameters["threshold"] == {"type": "float", "default": 4.0, "low": 0.0, "high": 10.0,
                                       "help": "Items whose value exceeds the threshold are picked."}
    assert parameters["factor"]["low"] == 0.8 and parameters["factor"]["high"] == 1.5 and parameters["factor"]["default"] == 1.0
    assert "crash" not in parameters, "a fixed setting is not tuned"
    assert parameters["a_values"]["type"] == "float" and parameters["a_values"]["default"] == 1.0
    assert "a factor on value in items of the rows where kind = 'a'" in parameters["a_values"]["help"]


def test_the_stages_run_the_harness_once_per_training_case(tmp_path, harness):
    config, _ = _compile(tmp_path, harness)
    static, full = config["evaluate"]["stages"]
    assert static == {"id": "static", "kind": "builtin-static"}
    assert full["instances"] == ["../../cases/a.json", "../../cases/b.json", "../../cases/c.json"]
    assert full["command"] == (
        "C:/work/.venv/Scripts/python.exe ../../harness/runner.py solve --case {instance} --seed {seed} "
        "--values {params_json} --study study-run.json --time-limit 5"
    )
    assert (full["seeds"], full["retries"], full["workers"], full["pin_cpus"]) == (2, 1, 3, [2, 4, 6])
    assert full["timeout"] == 5 * 1.5 + 30 and full["kpis_from"] == "stdout" and full["normalize"] == "none"
    assert "../../cases/d.json" not in json.dumps(config), "the test cases never reach the search"


def test_a_screen_runs_first_when_the_plan_says_so(tmp_path, harness):
    document = _with(cases__training=[f"cases/t{i}.json" for i in range(9)], limits__time_per_case_s=20)
    plan = make_plan(training=9, test=1, runs_per_case=2, time_limit_s=20, overhead_s=1, hours=3, workers=3, pin_cpus=[2, 4, 6])
    config, _ = _compile(tmp_path, harness, document, plan)
    screen = config["evaluate"]["stages"][1]
    assert screen["id"] == "screen" and screen["promote"] == {"top_k_per_generation": 2}
    assert len(screen["instances"]) == plan.screen_cases == 3 and screen["command"].endswith("--time-limit 5")
    assert screen["timeout"] == 5 * 1.5 + 30


def test_one_goal_level_is_the_objective_and_a_positive_kpi_counts_every_case_alike(tmp_path, harness):
    document = _with(goal={"levels": [{"kpi": "total", "direction": "lower"}]}, guardrails=[])
    config, _ = _compile(tmp_path, harness, document)
    assert config["evaluate"]["score"] == {"objective": "total", "direction": "minimize"}
    assert config["evaluate"]["stages"][1]["normalize"] == "baseline"
    assert "gates" not in config["evaluate"]


def test_levels_carry_their_tolerances(tmp_path, harness):
    config, _ = _compile(tmp_path, harness)
    assert config["evaluate"]["score"] == {"levels": [
        {"kpi": "picked_count", "direction": "maximize", "tolerance": 0.1, "relative": True},
        {"kpi": "penalty", "direction": "minimize"},
    ]}


def test_the_search_and_its_limits(tmp_path, harness):
    config, _ = _compile(tmp_path, harness)
    assert config["search"]["operators"] == OPERATORS and "models" not in config
    assert (config["search"]["children_per_generation"], config["search"]["generations"]) == (6, 9)
    assert config["stop"]["patience"] == 9
    assert config["budget"]["max_full_evals_per_day"] == 9 * 6 + 10
    assert config["search"]["novelty"] == {"behavioural": "off"}


def test_ai_search_help_needs_a_model(tmp_path, harness):
    document = _with(budget={"hours": 1, "ai": {"enabled": True, "max_usd": 3.0}})
    with pytest.raises(HarnessError, match="budget.ai: AI search help is on, and no model is set up"):
        _compile(tmp_path, harness, document)
    models = {"small": {"provider": "fake", "model": "s"}, "strong": {"provider": "fake", "model": "s"}}
    config, _ = _compile(tmp_path, harness, document, models=models)
    assert config["search"]["operators"] == OPERATORS_WITH_AI and config["models"] == models
    assert config["budget"]["max_usd"] == 3.0


def test_study_run_json_holds_everything_static(tmp_path, harness):
    _, study_run = _compile(tmp_path, harness)
    assert study_run["settings"] == {"base": {"threshold": 4.0, "factor": 1.0, "crash": False},
                                     "tuned": ["threshold", "factor"]}
    assert study_run["levers"]["a_values"] == {"lever": "item_values", "table": "items", "column": "value",
                                               "where": "kind = 'a'", "mode": "scale", "code": False, "integer": False}
    assert study_run["levers"]["b_boost"]["code"] is True and study_run["levers"]["b_boost"]["column"] is None
    assert study_run["kpis"]["total"] == {"direction": "lower", "sql": "SELECT total FROM summary"}
    assert study_run["kpis"]["picked_count"] == {"direction": "higher", "measure": True}
    assert study_run["kpis"]["a_value"]["params"] == {"kind": "a"}
    assert study_run["kpis"]["penalty"] == {"direction": "lower", "weighted": {"heavy": 1.0, "a_value": 0.5}}
    assert study_run["constraints"][1] == {"says": "No item above 50", "sql": "SELECT COUNT(*) = 0 FROM items WHERE value > 50"}
    assert study_run["guardrails"] == [{"kpi": "total", "max": 500.0}]
    assert study_run["inputs"] == {"start": "../../inputs/start.json"}
    assert study_run["tables"]["items"]["value"] == "float" and study_run["time_limit_s"] == 5


def test_a_start_outside_the_range_is_refused(tmp_path, harness):
    root = _study_folder(tmp_path)
    (root / "inputs" / "start.json").write_text(json.dumps({"factor": 1.9}), encoding="utf-8")
    document = _with(vary__settings__factor={"tune": True, "low": 0.8, "high": 1.5})
    with pytest.raises(HarnessError, match=r"vary\.settings\.factor: the starting value 1\.9 lies outside the range"):
        compile_study(Study.parse(document), harness, PLAN, root=root)


def test_a_blocked_plan_does_not_compile(tmp_path, harness):
    blocked = make_plan(training=3, test=3, runs_per_case=1, time_limit_s=600, hours=0.2, workers=1)
    with pytest.raises(HarnessError, match="plan: With these choices"):
        _compile(tmp_path, harness, plan=blocked)


def test_a_program_harness_runs_the_runner_under_evolvekit_with_the_program_named(tmp_path, harness):
    program = harness.__class__(**{**harness.__dict__, "application": harness.application.__class__(
        kind="program", label="A solver", probe="{app} --version")})
    config, _ = _compile(tmp_path, program, _with(application__path="C:/Program Files/Solver/solver.exe"))
    assert config["evaluate"]["stages"][1]["command"].startswith(
        '{python} ../../harness/runner.py --app "C:/Program Files/Solver/solver.exe" solve --case {instance}'
    )


def test_write_run_puts_the_config_the_runner_input_and_the_plan_in_the_run_directory(tmp_path, harness):
    root = _study_folder(tmp_path)
    run_dir = write_run(Study.parse(EXAMPLE), harness, PLAN, root, "20260926-1740")
    assert run_dir == root / "runs" / "20260926-1740"
    config = yaml.safe_load((run_dir / "evolvekit.yaml").read_text(encoding="utf-8"))
    assert build_config(config, base_dir=run_dir).final_stage.id == "full"
    assert json.loads((run_dir / "study-run.json").read_text())["harness"] == {"id": "toy", "version": "1.0.0"}
    assert json.loads((run_dir / "plan.json").read_text())["children"] == 6
