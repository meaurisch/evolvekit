"""Model-free operators that use what the run has already paid for.

`param_lhs` never looks at a score. These do: a local step from a good
configuration, a cross of two, and a Parzen estimator over everything evaluated
so far -- all plain arithmetic on a declared `problem.parameters` space.
"""

from __future__ import annotations

import random
import sys
from statistics import fmean

import pytest

from evolvekit.candidate import Candidate
from evolvekit.config import ConfigError, build_config
from evolvekit.search.operators import param_cross, param_local, param_tpe
from evolvekit.search.tuning import MIN_OBSERVATIONS, Observation, propose_tpe
from evolvekit.space import ParameterSpace


def _raw_space(extra: int = 24) -> dict:
    """A few parameters that matter among many that do not -- the usual case."""
    raw = {
        "neighbours": {"type": "int", "low": 10, "high": 120, "default": 60},
        "penalty": {"type": "float", "low": 1e2, "high": 1e6, "default": 1e5, "log": True},
        "exhaustive": {"type": "bool", "default": True},
        "init": {"type": "choice", "choices": ["greedy", "savings", "sweep"], "default": "greedy"},
    }
    for index in range(extra):
        raw[f"inert_{index:02d}"] = {"type": "float", "low": 0.0, "high": 1.0, "default": 0.5}
    return raw


def _wide_space(extra: int = 24) -> ParameterSpace:
    return ParameterSpace.parse(_raw_space(extra))


def _fitness(values: dict) -> float:
    """Higher is better. Best: neighbours 30, penalty 1e3, exhaustive off, savings."""
    space = _wide_space(0)
    by_name = {p.name: p for p in space}
    loss = 4.0 * abs(by_name["neighbours"].to_unit(values["neighbours"]) - by_name["neighbours"].to_unit(30))
    loss += 4.0 * abs(by_name["penalty"].to_unit(values["penalty"]) - 0.25)
    loss += 1.5 if values["exhaustive"] else 0.0
    loss += {"greedy": 1.0, "savings": 0.0, "sweep": 0.5}[values["init"]]
    return -loss


def _candidate(space: ParameterSpace, cid: str, values: dict, **fields) -> Candidate:
    return Candidate(
        id=cid, generation=1, block=space.render_block(values), source="", operator="param_lhs",
        params=values, **fields,
    )


# -- a local step ----------------------------------------------------------


def test_a_local_step_moves_one_to_three_parameters_however_many_there_are():
    space, rng = _wide_space(), random.Random(3)
    base = space.defaults()
    moved = [sum(1 for k in base if child[k] != base[k]) for child in (space.perturb(base, rng) for _ in range(400))]
    assert set(moved) == {1, 2, 3}, "never none -- a wasted evaluation -- and never a jump"
    assert moved.count(1) > moved.count(2) > moved.count(3), "one parameter says most about that parameter"


def test_in_a_small_space_a_local_step_is_one_parameter():
    space, rng = _wide_space(extra=3), random.Random(3)
    base = space.defaults()
    assert {sum(1 for k in base if child[k] != base[k]) for child in (space.perturb(base, rng) for _ in range(200))} == {1}


def test_an_integer_that_is_chosen_to_move_does_move():
    space = ParameterSpace.parse({"restarts": {"type": "int", "low": 1, "high": 1000, "default": 500}})
    rng = random.Random(1)
    children = [space.perturb({"restarts": 500}, rng, scale=0.0001)["restarts"] for _ in range(50)]
    assert set(children) == {499, 501}, "a step that rounds away becomes one whole unit"
    assert {space.perturb({"restarts": 1000}, rng, scale=0.0001)["restarts"] for _ in range(20)} == {999}


def test_param_local_renders_a_neighbour_of_its_parent():
    space = _wide_space()
    parent = _candidate(space, "g001-c0002", {**space.defaults(), "neighbours": 33})
    result = param_local(space, parent, seed=5)
    assert result.ok and result.mode == "param_local"
    child = result.meta["params"]
    assert child != parent.params and space.validate(child)[1] == []
    assert 1 <= sum(1 for k in child if child[k] != parent.params[k]) <= 3
    assert result.block == space.render_block(child)


# -- crossing two configurations -------------------------------------------


def test_param_cross_takes_every_parameter_from_one_of_the_two():
    space = _wide_space(4)
    a = _candidate(space, "g001-c0002", {**space.defaults(), "neighbours": 20, "init": "savings"})
    b = _candidate(space, "g001-c0003", {**space.defaults(), "penalty": 500.0, "exhaustive": False})
    seen = set()
    for seed in range(40):
        result = param_cross(space, a, b, seed=seed)
        child = result.meta["params"]
        assert space.validate(child)[1] == []
        if result.mode == "param_cross":
            assert result.meta["mate_id"] == b.id
            assert all(child[k] in (a.params[k], b.params[k]) for k in child)
            seen.add((child["neighbours"], child["exhaustive"]))
    assert (20, False) in seen, "gains that were found separately get tried together"


def test_with_nothing_to_cross_with_it_is_a_local_step_and_says_so():
    space = _wide_space(4)
    parent = _candidate(space, "g001-c0002", space.defaults())
    for mate in (None, _candidate(space, "g001-c0003", space.defaults())):
        result = param_cross(space, parent, mate, seed=1)
        assert result.mode == "param_local" and result.meta["fallback_of"] == "param_cross"
        assert result.meta["params"] != parent.params


# -- the estimator ---------------------------------------------------------


def _history(space: ParameterSpace, count: int, seed: int) -> list[Observation]:
    rng = random.Random(seed)
    return [Observation(v, _fitness(v)) for v in space.latin_hypercube(count, rng)]


def test_too_few_observations_is_nothing_to_estimate_from():
    space = _wide_space()
    assert propose_tpe(space, _history(space, MIN_OBSERVATIONS - 1, 1), random.Random(1)) is None
    assert propose_tpe(space, _history(space, MIN_OBSERVATIONS, 1), random.Random(1)) is not None


def test_the_estimator_finds_the_four_parameters_that_matter_among_twenty_eight():
    space = _wide_space()
    history = _history(space, 40, seed=7)
    rng = random.Random(11)
    proposals = [propose_tpe(space, history, rng) for _ in range(40)]
    sampled = space.latin_hypercube(40, random.Random(12))
    assert all(space.validate(p)[1] == [] for p in proposals)
    assert fmean(map(_fitness, proposals)) > fmean(map(_fitness, sampled)) + 1.0
    assert sum(1 for p in proposals if not p["exhaustive"]) >= 30, "it has learnt what the boolean costs"
    assert sum(1 for p in proposals if p["init"] != "greedy") >= 30


def test_it_never_proposes_what_has_already_been_evaluated():
    space = ParameterSpace.parse({"on": {"type": "bool", "default": True}, "mode": {"type": "choice", "choices": ["a", "b"], "default": "a"}})
    every = [{"on": on, "mode": mode} for on in (True, False) for mode in ("a", "b")]
    history = [Observation(v, float(i)) for i, v in enumerate(every)] * 2
    assert propose_tpe(space, history, random.Random(1)) is None, "four configurations, all seen"
    assert propose_tpe(space, history[:3] * 3, random.Random(1)) == every[3]


def test_a_region_where_everything_crashed_is_not_proposed_again():
    space = _wide_space(2)
    rng = random.Random(5)
    history = []
    for values in space.latin_hypercube(36, rng):
        crashed = values["init"] == "sweep"
        history.append(Observation(values, -100.0 if crashed else _fitness(values)))
    proposals = [propose_tpe(space, history, random.Random(s)) for s in range(30)]
    assert sum(1 for p in proposals if p["init"] == "sweep") <= 2


def test_param_tpe_is_a_local_step_until_there_is_something_to_learn_from():
    space = _wide_space(2)
    parent = _candidate(space, "g000-c0001", space.defaults())
    early = param_tpe(space, parent, _history(space, 3, 1), seed=2)
    assert early.mode == "param_local" and early.meta["fallback_of"] == "param_tpe"
    later = param_tpe(space, parent, _history(space, 30, 1), seed=2)
    assert later.mode == "param_tpe" and later.meta["observations"] == 30


# -- configuration and the whole loop --------------------------------------


def test_an_operator_on_a_declared_space_needs_a_declared_space(tmp_path, minimal_raw):
    minimal_raw["search"] = {"operators": {"rewrite": 0.5, "param_tpe": 0.5}}
    with pytest.raises(ConfigError, match=r"search\.operators\.param_tpe: works on a declared parameter space"):
        build_config(minimal_raw, base_dir=tmp_path)


SOLVER = (
    "import argparse, json, math\n"
    "p = argparse.ArgumentParser()\n"
    "p.add_argument('--neighbours', type=int); p.add_argument('--penalty', type=float)\n"
    "p.add_argument('--exhaustive'); p.add_argument('--init')\n"
    "for i in range(6): p.add_argument('--inert-%02d' % i, type=float)\n"
    "a = p.parse_args()\n"
    "cost = 1000 + 3 * abs(a.neighbours - 30) + 40 * abs(math.log10(a.penalty) - 3)\n"
    "cost += (30 if a.exhaustive == 'true' else 0) + {'greedy': 25, 'savings': 0, 'sweep': 10}[a.init]\n"
    "print(json.dumps({'cost': cost}))\n"
)


@pytest.mark.slow
def test_a_model_free_run_uses_all_four_operators_and_none_of_them_needs_a_model(tmp_path):
    from evolvekit.search.driver import Driver

    (tmp_path / "solver.py").write_text(SOLVER, encoding="utf-8")
    config = build_config(
        {
            "problem": {"parameters": _raw_space(6)},
            "evaluate": {
                "stages": [
                    {"id": "static", "kind": "builtin-static"},
                    {"id": "full", "kind": "command", "kpis_from": "stdout",
                     "command": f'"{sys.executable}" solver.py {{params}}', "timeout": 60},
                ],
                "score": {"objective": "cost", "direction": "minimize"},
            },
            "search": {
                "operators": {"param_lhs": 0.2, "param_local": 0.4, "param_tpe": 0.3, "param_cross": 0.1},
                "children_per_generation": 6, "generations": 6, "seed": 4,
                "novelty": {"behavioural": "off"},
            },
            "budget": {"max_full_evals_per_day": 200},
            "stop": {"patience": 20},
        },
        base_dir=tmp_path,
    )
    assert config.models is None and not config.search.uses_llm
    driver = Driver(config, run_dir=tmp_path / "run")
    summary = driver.run()
    rows = driver.ledger.runs()
    operators = {r["operator"] for r in rows[1:]}
    assert operators >= {"param_lhs", "param_local", "param_tpe"}, operators
    assert summary.totals.get("calls", 0.0) == 0.0
    assert summary.best.score > summary.seed_score + 40, "the defaults cost 1225; the search is well below 1185"
    local = [r for r in rows[1:] if r["operator"] == "param_local" and r["parent_id"]]
    by_id = {r["id"]: r for r in rows}
    steps = [sum(1 for k, v in r["params"].items() if by_id[r["parent_id"]]["params"][k] != v) for r in local]
    assert steps and max(steps) <= 3, "a local step stays local"
