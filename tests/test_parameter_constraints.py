"""`problem.parameter_constraints`: conditions on the tuned values, checked
before any solving -- in the config, in the static stage, and by the
model-free operators, which draw again rather than propose a configuration
that breaks one."""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from evolvekit.candidate import Candidate, splice_block
from evolvekit.config import ConfigError, build_config
from evolvekit.evaluate.cascade import Cascade
from evolvekit.evaluate.stages import run_static_stage
from evolvekit.prompts import system_prompt
from evolvekit.search.driver import Driver
from evolvekit.space import CONSTRAINT_ATTEMPTS, ParameterSpace, SpaceError

SPACE = {
    "van": {"type": "float", "low": 0.5, "high": 2.0, "default": 1.0},
    "truck": {"type": "float", "low": 0.5, "high": 2.0, "default": 1.5},
    "mode": {"type": "choice", "choices": ["fast", "slow"], "default": "fast"},
}

SOLVER = (
    "import argparse, json, pathlib\n"
    "p = argparse.ArgumentParser()\n"
    "p.add_argument('--van', type=float); p.add_argument('--truck', type=float); p.add_argument('--mode')\n"
    "a = p.parse_args()\n"
    "pathlib.Path('solver-ran').write_text('yes')\n"
    "print(json.dumps({'cost': a.van + a.truck}))\n"
)


def _raw(constraints, **problem):
    return {
        "problem": {"parameters": SPACE, "parameter_constraints": constraints, **problem},
        "evaluate": {
            "stages": [
                {"id": "static", "kind": "builtin-static"},
                {"id": "full", "kind": "command", "kpis_from": "stdout", "command": "{python} solver.py {params}"},
            ],
            "score": {"objective": "cost", "direction": "minimize"},
        },
        "search": {"operators": {"param_local": 1.0}},
    }


def _config(tmp_path: Path, constraints):
    (tmp_path / "solver.py").write_text(SOLVER, encoding="utf-8")
    return build_config(_raw(constraints), base_dir=tmp_path)


def test_a_constraint_is_an_expression_or_an_expression_with_a_sentence(tmp_path):
    config = _config(
        tmp_path,
        ["truck >= van", {"expr": "mode == 'fast' or van < 1.2", "says": "Slow mode only with cheap vans"}],
    )
    first, second = config.problem.parameters.constraints
    assert (first.text, first.says) == ("truck >= van", "truck >= van")
    assert (second.text, second.says) == ("mode == 'fast' or van < 1.2", "Slow mode only with cheap vans")


@pytest.mark.parametrize(
    "constraints, message",
    [
        (["lorry >= van"], r"problem\.parameter_constraints\[0\]: 'lorry' is not a parameter"),
        (["truck >= "], r"problem\.parameter_constraints\[0\]: .*not a valid expression"),
        (["truck + van"], r"problem\.parameter_constraints\[0\]: .*not a condition"),
        (["van >= truck"], r"problem\.parameter_constraints\[0\]: the defaults break it .*truck=1\.5, van=1\.0"),
        ([{"expr": "truck >= van", "because": "x"}], r"problem\.parameter_constraints\[0\]: unknown key"),
        ([{"says": "no expression"}], r"problem\.parameter_constraints\[0\]\.expr: required"),
        ([42], r"problem\.parameter_constraints\[0\]: expected an expression"),
        ("truck >= van", r"problem\.parameter_constraints: expected a list"),
        (["truck >= van", "van <= truck", "truck.x > 1"], r"problem\.parameter_constraints\[2\]: 'truck\.x > 1': attribute access"),
        (["van / (truck - 1.5) > 0"], r"problem\.parameter_constraints\[0\]: .*cannot be computed at the defaults: division by zero"),
    ],
)
def test_a_constraint_that_cannot_work_is_a_config_error(tmp_path, constraints, message):
    with pytest.raises(ConfigError, match=message):
        _config(tmp_path, constraints)


def test_constraints_need_a_declared_space(tmp_path):
    (tmp_path / "skeleton.py").write_text(
        "# EVOLVE-BLOCK-START\ndef f():\n    return 1\n# EVOLVE-BLOCK-END\n", encoding="utf-8"
    )
    raw = _raw(["a > 1"])
    raw["problem"] = {"skeleton": "skeleton.py", "parameter_constraints": ["a > 1"]}
    raw["evaluate"]["stages"][1]["command"] = "{python} solver.py {candidate}"
    raw["search"] = {"operators": {"param_lhs": 1.0}}
    with pytest.raises(ConfigError, match=r"problem\.parameter_constraints: .*problem\.parameters"):
        build_config(raw, base_dir=tmp_path)


def test_a_violation_is_one_sentence_naming_the_values(tmp_path):
    space = _config(tmp_path, ["truck >= van", {"expr": "van < 1.8", "says": "Vans stay cheap"}]).problem.parameters
    broken = space.violations({"van": 1.9, "truck": 0.6, "mode": "fast"})
    assert broken == [
        "breaks the constraint `truck >= van` (truck=0.6, van=1.9)",
        'breaks the constraint "Vans stay cheap" (`van < 1.8`: van=1.9)',
    ]
    assert space.satisfies({"van": 1.0, "truck": 1.5, "mode": "fast"})
    assert not space.satisfies({"van": 1.9, "truck": 0.6, "mode": "fast"})


def test_a_constraint_that_cannot_be_computed_counts_as_broken():
    space = ParameterSpace.parse(
        {"a": {"type": "float", "low": 0, "high": 1, "default": 1}, "b": {"type": "float", "low": 0, "high": 1, "default": 1}}
    ).with_constraints(["a / b >= 0.5"])
    assert space.violations({"a": 1.0, "b": 0.0}) == [
        "cannot check the constraint `a / b >= 0.5` (a=1.0, b=0.0): division by zero"
    ]


def _module(tmp_path: Path, values: dict) -> tuple[Path, str]:
    source = f"# EVOLVE-BLOCK-START\ndef configure():\n    return {values!r}\n# EVOLVE-BLOCK-END\n"
    path = tmp_path / "candidate.py"
    path.write_text(source, encoding="utf-8")
    return path, source


def test_the_static_stage_refuses_a_configuration_that_breaks_a_constraint(tmp_path):
    config = _config(tmp_path, ["truck >= van"])
    path, source = _module(tmp_path, {"van": 1.9, "truck": 0.6, "mode": "fast"})
    outcome = run_static_stage(path, source, config.evaluate.stages[0], config.problem)
    assert not outcome.ok and outcome.params is None
    assert "the configuration breaks the constraint `truck >= van` (truck=0.6, van=1.9)" in outcome.failure


def test_the_solver_never_runs_for_a_configuration_that_breaks_a_constraint(tmp_path):
    config = _config(tmp_path, ["truck >= van"])
    cascade = Cascade(config, work_dir=tmp_path / "work")
    block = config.problem.parameters.render_block({"van": 1.9, "truck": 0.6, "mode": "fast"})
    source = splice_block(
        config.problem.skeleton_source(), block, config.problem.block_start, config.problem.block_end
    )
    candidate = Candidate(id="g001-c0001", generation=1, block=block, source=source, operator="param_local")
    result = cascade.evaluate_generation([candidate])["g001-c0001"]
    assert result.rejected and "breaks the constraint" in (result.reject_reason or "")
    assert not (tmp_path / "solver-ran").exists(), "the command must never start"


def test_the_prompt_lists_the_constraints_under_the_parameters(tmp_path):
    config = _config(tmp_path, ["truck >= van", {"expr": "van < 1.8", "says": "Vans stay cheap"}])
    prompt = system_prompt(config, "# prefix\n", "# suffix\n")
    parameters = prompt.index("## Parameters `configure()` returns")
    constraints = prompt.index("Constraints on these values")
    assert parameters < constraints
    assert "- `truck >= van`" in prompt
    assert "- Vans stay cheap: `van < 1.8`" in prompt


def test_the_run_describes_its_constraints(tmp_path):
    config = _config(tmp_path, ["truck >= van"])
    driver = Driver(config, run_dir=tmp_path / "run")
    assert driver._describe_run(first_generation=1, planned=1)["parameter_constraints"] == ["truck >= van"]


def test_draw_takes_the_first_proposal_that_fits():
    space = ParameterSpace.parse(SPACE).with_constraints(["truck >= van"])
    proposals = iter([{"van": 2.0, "truck": 0.5, "mode": "fast"}, {"van": 0.6, "truck": 0.7, "mode": "slow"}])
    assert space.draw(lambda: next(proposals)) == {"van": 0.6, "truck": 0.7, "mode": "slow"}


def test_draw_gives_up_after_a_hundred_proposals_and_returns_the_last():
    space = ParameterSpace.parse(SPACE).with_constraints(["truck >= van"])
    calls = []

    def propose():
        calls.append(1)
        return {"van": 2.0, "truck": 0.5, "mode": "fast"}

    assert space.draw(propose) == {"van": 2.0, "truck": 0.5, "mode": "fast"}
    assert len(calls) == CONSTRAINT_ATTEMPTS == 100


def test_draw_without_constraints_takes_the_first_proposal():
    space = ParameterSpace.parse(SPACE)
    rng = random.Random(1)
    first = space.perturb(space.defaults(), random.Random(1))
    assert space.draw(lambda: space.perturb(space.defaults(), rng)) == first


def test_with_constraints_is_a_space_error_for_the_space_itself():
    with pytest.raises(SpaceError, match=r"x\[0\]: 'q' is not a parameter"):
        ParameterSpace.parse(SPACE).with_constraints(["q > 1"], path="x")


# -- the model-free operators draw again rather than propose a breaking configuration

from evolvekit.search.operators import param_cross, param_lhs_typed, param_local, param_tpe  # noqa: E402
from evolvekit.search.tuning import Observation  # noqa: E402

ORDERED = ParameterSpace.parse(
    {"a": {"type": "float", "low": 0.0, "high": 1.0, "default": 0.2},
     "b": {"type": "float", "low": 0.0, "high": 1.0, "default": 0.8}}
).with_constraints(["a <= b"])


def _parent(space, values, cid="g001-c0001"):
    return Candidate(
        id=cid, generation=1, block=space.render_block(values), source="", operator="param_local", params=dict(values)
    )


@pytest.mark.parametrize("seed", range(60))
def test_every_model_free_operator_respects_the_constraints(seed):
    # Parent and mate sit close to the line a == b: half of what an operator
    # would propose unconstrained is on the wrong side of it.
    parent = _parent(ORDERED, {"a": 0.45, "b": 0.5})
    mate = _parent(ORDERED, {"a": 0.6, "b": 0.65}, "g001-c0002")
    rng = random.Random(seed)
    observations = []
    for _ in range(12):
        a = rng.random() * 0.9
        observations.append(Observation({"a": a, "b": a + rng.random() * (1 - a)}, rng.random()))
    for result in (
        param_lhs_typed(ORDERED, parent, seed=seed),
        param_local(ORDERED, parent, seed=seed),
        param_cross(ORDERED, parent, mate, seed=seed),
        param_tpe(ORDERED, parent, observations, seed=seed),
    ):
        assert result.ok, result.error
        values = result.meta["params"]
        assert values["a"] <= values["b"], (result.operator, values)


def test_a_constraint_nothing_can_meet_leaves_the_last_draw_to_the_static_stage():
    pinned = ParameterSpace.parse(
        {"a": {"type": "float", "low": 0.0, "high": 1.0, "default": 0.2},
         "b": {"type": "float", "low": 0.0, "high": 1.0, "default": 0.8}}
    ).with_constraints(["a == 0.2 and b == 0.8"])
    result = param_local(pinned, _parent(pinned, pinned.defaults()), seed=3)
    assert result.ok
    assert pinned.violations(result.meta["params"]), "every move breaks it; the static stage refuses the child"


def test_without_constraints_the_operators_propose_what_they_always_did():
    free = ParameterSpace.parse(
        {"a": {"type": "float", "low": 0.0, "high": 1.0, "default": 0.2},
         "b": {"type": "float", "low": 0.0, "high": 1.0, "default": 0.8}}
    )
    parent = _parent(free, {"a": 0.45, "b": 0.5})
    assert param_local(free, parent, seed=7).meta["params"] == free.perturb({"a": 0.45, "b": 0.5}, random.Random(7))
