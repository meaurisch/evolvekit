"""A run directory belongs to one problem.

`runs.jsonl` is append-only and the archive is rebuilt from it. Point a second
problem -- or the same one with another time limit -- at a directory that has a
run in it, and the new run breeds from the old one's candidates and ranks
scores against each other that do not mean the same thing. It used to do that
without a word.
"""

from __future__ import annotations

import copy

import pytest

from evolvekit.cli import main
from evolvekit.config import build_config
from evolvekit.search.driver import Driver

SOLVER = (
    "import argparse, json\n"
    "p = argparse.ArgumentParser(); p.add_argument('--x', type=float); p.add_argument('--scale', type=float, default=1.0)\n"
    "a = p.parse_args()\n"
    "print(json.dumps({'cost': a.scale * (100 + abs(a.x - 0.3)), 'gain': -abs(a.x - 0.3)}))\n"
)
RAW = {
    "problem": {"parameters": {"x": {"type": "float", "low": 0.0, "high": 1.0, "default": 0.9}}},
    "evaluate": {
        "stages": [
            {"id": "static", "kind": "builtin-static"},
            {"id": "full", "kind": "command", "kpis_from": "stdout", "command": "{python} solver.py {params}"},
        ],
        "score": {"objective": "cost", "direction": "minimize"},
    },
    "search": {"operators": {"param_lhs": 1.0}, "children_per_generation": 2, "generations": 1, "seed": 1},
    "budget": {"max_full_evals_per_day": 100},
}


def _run(tmp_path, raw, **driver):
    (tmp_path / "solver.py").write_text(SOLVER, encoding="utf-8")
    config = build_config(raw, base_dir=tmp_path)
    instance = Driver(config, run_dir=tmp_path / "run", log=lambda m: None, **driver)
    return instance, instance.run(generations=1)  # one *more*, whatever the directory holds


@pytest.mark.slow
def test_the_same_problem_continues_whatever_else_changed(tmp_path):
    _run(tmp_path, RAW)
    later = copy.deepcopy(RAW)
    later["search"].update({"children_per_generation": 3, "operators": {"param_lhs": 0.5, "param_local": 0.5}})
    later["budget"]["max_full_evals_per_day"] = 50
    driver, summary = _run(tmp_path, later)
    assert summary.generations == 1 and len(driver.ledger.runs()) == 1 + 2 + 3


@pytest.mark.slow
@pytest.mark.parametrize(
    "change, said",
    [
        (lambda raw: raw["evaluate"]["stages"][1].update(command="{python} solver.py --scale 2 {params}"), "stage 'full': `command` changed"),
        (lambda raw: raw["evaluate"]["score"].update(objective="gain", direction="maximize"), "the objective is now 'gain', was 'cost'"),
        (lambda raw: raw["problem"]["parameters"].update(y={"type": "bool", "default": True}), "the declared parameters changed"),
    ],
)
def test_a_different_problem_is_refused_before_anything_is_bred_or_recorded(tmp_path, change, said):
    first, _ = _run(tmp_path, RAW)
    recorded = len(first.ledger.runs())
    other = copy.deepcopy(RAW)
    change(other)
    with pytest.raises(ValueError) as error:
        _run(tmp_path, other)
    message = str(error.value)
    assert "holds a run of a different problem" in message and said in message
    assert "--allow-changed-problem" in message and "new --run-dir" in message
    assert len(first.ledger.runs()) == recorded, "nothing was appended"


@pytest.mark.slow
def test_whoever_knows_better_can_say_so(tmp_path):
    _run(tmp_path, RAW)
    other = copy.deepcopy(RAW)
    other["evaluate"]["stages"][1]["command"] = "{python} solver.py --scale 1.0 {params}"
    driver, summary = _run(tmp_path, other, allow_changed_problem=True)
    assert summary.generations == 1 and len(driver.ledger.runs()) == 5


@pytest.mark.slow
def test_on_the_command_line_it_is_an_error_with_exit_code_1(tmp_path, capsys):
    import yaml

    (tmp_path / "solver.py").write_text(SOLVER, encoding="utf-8")
    config_path = tmp_path / "evolvekit.yaml"
    config_path.write_text(yaml.safe_dump(RAW, sort_keys=False), encoding="utf-8")
    assert main(["run", "--config", str(config_path), "--run-dir", str(tmp_path / "run"), "--quiet"]) == 0
    other = copy.deepcopy(RAW)
    other["evaluate"]["stages"][1]["command"] = "{python} solver.py --scale 3 {params}"
    config_path.write_text(yaml.safe_dump(other, sort_keys=False), encoding="utf-8")
    assert main(["run", "--config", str(config_path), "--run-dir", str(tmp_path / "run"), "--quiet"]) == 1
    assert "holds a run of a different problem" in capsys.readouterr().err
    assert main(["run", "--config", str(config_path), "--run-dir", str(tmp_path / "run"), "--quiet", "--allow-changed-problem"]) == 0


@pytest.mark.slow
def test_a_refusal_leaves_the_directory_as_it_found_it(tmp_path):
    from evolvekit.events import read_events
    from evolvekit.status import build_status

    _run(tmp_path, RAW)
    before = len(read_events(tmp_path / "run"))
    other = copy.deepcopy(RAW)
    other["evaluate"]["score"].update(objective="gain", direction="maximize")
    with pytest.raises(ValueError):
        _run(tmp_path, other)
    assert len(read_events(tmp_path / "run")) == before
    assert build_status(tmp_path / "run")["health"]["state"] == "finished"


@pytest.mark.slow
def test_an_allowed_change_is_the_problem_from_then_on(tmp_path):
    """`--allow-changed-problem` is said once. Every later session is compared
    with what the directory holds *now* -- the last recorded problem -- not with
    what it was first started with, or the flag would be needed forever."""
    _run(tmp_path, RAW)
    other = copy.deepcopy(RAW)
    other["evaluate"]["stages"][1]["command"] = "{python} solver.py --scale 1.0 {params}"
    _run(tmp_path, other, allow_changed_problem=True)
    driver, summary = _run(tmp_path, other)
    assert summary.generations == 1 and len(driver.ledger.runs()) == 1 + 2 + 2 + 2
    with pytest.raises(ValueError, match="holds a run of a different problem"):
        _run(tmp_path, RAW)  # and going back is a change again


@pytest.mark.slow
def test_whitespace_in_a_command_is_not_a_different_problem(tmp_path):
    _run(tmp_path, RAW)
    spaced = copy.deepcopy(RAW)
    spaced["evaluate"]["stages"][1]["command"] = "{python}  solver.py   {params} "
    driver, summary = _run(tmp_path, spaced)
    assert summary.generations == 1
