"""The benchmark's tuning setup says what it claims: the declared defaults are
the solver's own, every tunable is declared, and the miniature config is a
faithful, runnable copy of the real one."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

from evolvekit.config import load_config

BENCH = Path(__file__).resolve().parents[1] / "benchmarks" / "pyvrp_hard"


def _solver_parameters() -> dict[str, tuple[type, object]]:
    spec = importlib.util.spec_from_file_location("pyvrp_hard_solve", BENCH / "solve.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # does not import pyvrp: parameters are checked before it is needed
    return {name: (kind, default) for name, kind, default, _help in module.PARAMETERS}


def test_the_declared_defaults_are_the_solvers_own_and_nothing_is_left_out():
    declared = yaml.safe_load((BENCH / "tuning.yaml").read_text(encoding="utf-8"))["problem"]["parameters"]
    solver = _solver_parameters()
    assert set(declared) == set(solver), "every tunable of solve.py, and nothing it does not know"
    kinds = {int: "int", float: "float", bool: "bool"}
    for name, (kind, default) in solver.items():
        assert declared[name]["type"] == kinds[kind], name
        assert declared[name]["default"] == default, f"{name}: the baseline has to be PyVRP's default"
        if kind is not bool:
            assert declared[name]["low"] <= default <= declared[name]["high"], name
    # The one constraint between two parameters holds for every point of the box.
    assert declared["min_perturbations"]["high"] <= declared["max_perturbations"]["low"]
    assert declared["min_penalty"]["high"] <= declared["max_penalty"]["low"]


def test_the_miniature_is_the_real_setup_with_smaller_numbers():
    smoke = load_config(BENCH / "tuning.smoke.yaml")
    real = yaml.safe_load((BENCH / "tuning.yaml").read_text(encoding="utf-8"))
    assert smoke.problem.parameters.names == list(real["problem"]["parameters"])
    assert smoke.models is None and not smoke.search.uses_llm
    assert dict(smoke.search.operators) == real["search"]["operators"]
    screen, full = smoke.evaluate.stages[1:]
    assert (screen.kpis_from, full.kpis_from) == ("stdout", "stdout")
    assert full.instance_names() == ("s01-n60-uniform-mixed", "s02-n120-metro-tight")
    assert all("{python} solve.py" in s.command and "{params}" in s.command for s in (screen, full))
    real_stages = {s["id"]: s for s in real["evaluate"]["stages"]}
    assert "--time-limit 600" in real_stages["full"]["command"] and real_stages["full"]["instances"] == ["instances/t*.json"]
    assert real_stages["full"]["workers"] == 3 and real_stages["full"]["pin_cpus"] == [2, 4, 6]
