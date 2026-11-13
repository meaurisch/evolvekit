"""Model-free operators that use what the run has already paid for.

`param_lhs` never looks at a score. These do: a local step from a good
configuration, a cross of two, and a Parzen estimator over everything evaluated
so far -- all plain arithmetic on a declared `problem.parameters` space.
"""

from __future__ import annotations

import json
import random
import sys
from statistics import fmean

import pytest

from evolvekit.candidate import Candidate
from evolvekit.config import ConfigError, build_config
from evolvekit.search.operators import param_cross, param_local, param_tpe
from evolvekit.search.tuning import MIN_OBSERVATIONS, Observation, propose_tpe
from evolvekit.space import ParameterSpace
from tests.test_params import sweepable_project  # noqa: F401  (a fixture)


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


def _screening_driver(tmp_path, *, normalized: bool, middle: bool = False):
    """A proxy stage on one input, then the full stage on three instances
    (with `middle`, a second screen on two instances between them)."""
    from evolvekit.search.driver import Driver

    stage = {"kind": "command", "kpis_from": "stdout", "command": "{python} solver.py --instance {instance} {params}",
             "normalize": "baseline" if normalized else "none"}
    config = build_config(
        {
            "problem": {"parameters": _raw_space(0)},
            "evaluate": {
                "stages": [
                    {"id": "static", "kind": "builtin-static"},
                    {"id": "proxy", **stage, "instances": ["small"]},
                    *([{"id": "middle", **stage, "instances": ["small", "medium"]}] if middle else []),
                    {"id": "full", **stage, "instances": ["small", "medium", "large"]},
                ],
                "score": {"objective": "cost", "direction": "minimize"},
            },
            "search": {"operators": {"param_tpe": 1.0}},
        },
        base_dir=tmp_path,
    )
    return Driver(config, run_dir=tmp_path / "run", log=lambda m: None)


@pytest.mark.parametrize("normalized", [True, False])
def test_observations_are_ranks_in_four_tiers(tmp_path, normalized):
    """Q4: what the estimator reads is a rank, best first: (1) finished, by
    fitness; (2) screened out, by the deepest stage reached and that stage's
    score -- on any scale, since only the order counts; (3) raced out or kept
    out by the hold-out veto, one shared rank; (4) failed or gated, one shared
    rank, the worst. A proxy's score of 9 on its one small instance beats both
    finished scores as a number and still ranks below them."""
    driver = _screening_driver(tmp_path, normalized=normalized)
    space = driver.config.problem.parameters
    full = ["static", "proxy", "full"]

    def at(n: int) -> dict:
        return {**space.defaults(), "neighbours": n}

    driver.archive = [
        _candidate(space, "g001-c0002", at(20), score=3.0, competes=True, stages_reached=full,
                   stage_scores={"proxy": 3.0, "full": 3.0}),
        _candidate(space, "g001-c0003", at(30), score=5.0, competes=True, stages_reached=full,
                   stage_scores={"proxy": 5.0, "full": 5.0}),
        _candidate(space, "g001-c0004", at(40), score=9.0, competes=False, stages_reached=["static", "proxy"],
                   stage_scores={"proxy": 9.0}),
        _candidate(space, "g001-c0005", at(50), score=20.0, competes=False, stages_reached=full,
                   stage_scores={"proxy": 20.0, "full": 20.0}, raced_out="raced out after 2 of 3 instances"),
        _candidate(space, "g001-c0006", at(60), score=50.0, competes=False, stages_reached=full,
                   stage_scores={"proxy": 50.0, "full": 50.0}, veto="holdout below the seed's 1.0"),
        _candidate(space, "g001-c0007", at(70), score=-1e9, competes=False, stages_reached=["static"],
                   last_failure="stage proxy: exit 1"),
        _candidate(space, "g001-c0008", at(80), score=60.0, competes=False, stages_reached=full,
                   gated="missed = 2 > 0"),
    ]
    seen = {o.values["neighbours"]: o.fitness for o in driver._observations()}
    assert set(seen) == {20, 30, 40, 50, 60, 70, 80}, "the screened one is an observation on any scale"
    assert seen[30] > seen[20] > seen[40] > seen[50], "finished by fitness, then screened, then raced out"
    assert seen[50] == seen[60], "raced out and vetoed share one rank"
    assert seen[60] > seen[70] == seen[80], "failed and gated share the worst rank"
    assert seen[30] == -1.0 and seen[20] == -2.0 and seen[40] == -3.0, "a rank is minus the position"


def test_a_deeper_screen_ranks_above_a_shallower_one_whatever_the_score(tmp_path):
    driver = _screening_driver(tmp_path, normalized=False, middle=True)
    space = driver.config.problem.parameters
    driver.archive = [
        _candidate(space, "g001-c0002", {**space.defaults(), "neighbours": 20}, score=99.0, competes=False,
                   stages_reached=["static", "proxy"], stage_scores={"proxy": 99.0}),
        _candidate(space, "g001-c0003", {**space.defaults(), "neighbours": 30}, score=-50.0, competes=False,
                   stages_reached=["static", "proxy", "middle"], stage_scores={"proxy": 1.0, "middle": -50.0}),
    ]
    seen = {o.values["neighbours"]: o.fitness for o in driver._observations()}
    assert seen[30] > seen[20], "reaching the second screen says more than a high score on the first"


def test_an_abandoned_final_stage_is_no_observation(tmp_path):
    """Reached the final stage, did not finish it, and nothing failed (a stop
    request, the time limit, the daily cap): that says nothing about the
    configuration, so the estimator is not told anything about it."""
    driver = _screening_driver(tmp_path, normalized=False)
    space = driver.config.problem.parameters
    driver.archive = [
        _candidate(space, "g001-c0002", {**space.defaults(), "neighbours": 20}, score=3.0, competes=True,
                   stages_reached=["static", "proxy", "full"], stage_scores={"full": 3.0}),
        _candidate(space, "g001-c0003", {**space.defaults(), "neighbours": 30}, score=8.0, competes=False,
                   stages_reached=["static", "proxy", "full"], stage_scores={"proxy": 8.0}),
    ]
    assert {o.values["neighbours"] for o in driver._observations()} == {20}


def test_tied_observations_share_the_mean_position(tmp_path):
    driver = _screening_driver(tmp_path, normalized=False)
    space = driver.config.problem.parameters
    full = ["static", "proxy", "full"]
    driver.archive = [
        _candidate(space, f"g001-c000{i}", {**space.defaults(), "neighbours": n}, score=s, competes=True,
                   stages_reached=full, stage_scores={"full": s})
        for i, (n, s) in enumerate([(20, 4.0), (30, 2.0), (40, 2.0), (50, 1.0)], start=2)
    ]
    seen = {o.values["neighbours"]: o.fitness for o in driver._observations()}
    assert seen == {20: -1.0, 30: -2.5, 40: -2.5, 50: -4.0}


def test_siblings_proposed_this_generation_push_tpe_away():
    """The constant liar (Q19): a configuration already proposed this
    generation joins the bad set, so the estimator rates its neighbourhood
    lower and the next proposal lands further from it. Same observations, same
    rng seed, with and without the sibling taken: over 200 seeds the mean
    distance from the sibling grows. (Before, `taken` only excluded the exact
    point, and the two proposals were the same.)"""
    space = _wide_space(0)
    history = _history(space, 30, seed=7)
    plain, liar = [], []
    for s in range(200):
        sibling = propose_tpe(space, history, random.Random(s))
        plain.append(space.distance(propose_tpe(space, history, random.Random(10_000 + s)), sibling))
        pushed = propose_tpe(space, history, random.Random(10_000 + s), taken=[sibling])
        liar.append(space.distance(pushed, sibling))
    print(f"mean distance from the sibling: {fmean(plain):.4f} without, {fmean(liar):.4f} with it taken")
    assert fmean(liar) > fmean(plain) + 0.05, "measured: 0.145 without, 0.252 with it taken"


def test_a_taken_configuration_does_not_count_towards_the_good_set():
    """`taken` is added after the split: it never changes `n_good`."""
    space = _wide_space(0)
    history = _history(space, MIN_OBSERVATIONS - 1, 1)
    taken = [o.values for o in _history(space, 5, 2)]
    assert propose_tpe(space, history, random.Random(1), taken=taken) is None, "taken points are not observations"


# -- one run-wide hypercube (Q3, Decision 8) -------------------------------


def _driver(tmp_path, *, operators, children, generations, space, constraints=None):
    from evolvekit.search.driver import Driver

    problem: dict = {"parameters": {name: {"type": "float", "low": lo, "high": hi, "default": (lo + hi) / 2}
                                    for name, (lo, hi) in space.items()}}
    if constraints:
        problem["parameter_constraints"] = constraints
    config = build_config(
        {
            "problem": problem,
            "evaluate": {
                "stages": [
                    {"id": "static", "kind": "builtin-static"},
                    {"id": "full", "kind": "command", "kpis_from": "stdout", "command": "{python} solver.py {params}"},
                ],
                "score": {"objective": "cost", "direction": "minimize"},
            },
            "search": {"operators": operators, "children_per_generation": children, "generations": generations},
        },
        base_dir=tmp_path,
    )
    return Driver(config, run_dir=tmp_path / "run", log=lambda m: None)


def _lhs_state(tmp_path) -> dict:
    return json.loads((tmp_path / "run" / "work" / "lhs.json").read_text(encoding="utf-8"))


def test_the_lhs_children_of_a_run_share_one_hypercube(tmp_path):
    """Q3, Decision 8: with the default shares a generation has 0-2 param_lhs children;
    over the run they take the rows of one cube, so the first K cover K strata of every
    parameter. generations 2 x children 4 x share 0.5 -> 4 expected -> K = 4."""
    driver = _driver(tmp_path, operators={"param_lhs": 0.5, "param_local": 0.5}, children=4, generations=2,
                     space={"x": (0.0, 1.0), "y": (0.0, 1.0)})
    rows = [driver._next_lhs_row(driver._seed_candidate()) for _ in range(4)]
    for name in ("x", "y"):
        assert sorted(int(row[name] * 4) for row in rows) == [0, 1, 2, 3]
    assert _lhs_state(tmp_path)["cubes"]["typed"]["K"] == 4


def test_the_cube_size_is_clamped_to_4_and_64(tmp_path):
    small = _driver(tmp_path / "a", operators={"param_lhs": 0.1, "param_local": 0.9}, children=2, generations=2,
                    space={"x": (0.0, 1.0)})
    big = _driver(tmp_path / "b", operators={"param_lhs": 1.0}, children=20, generations=50, space={"x": (0.0, 1.0)})
    small._next_lhs_row(small._seed_candidate())
    big._next_lhs_row(big._seed_candidate())
    assert _lhs_state(tmp_path / "a")["cubes"]["typed"]["K"] == 4
    assert _lhs_state(tmp_path / "b")["cubes"]["typed"]["K"] == 64


def test_the_cube_is_kept_and_the_next_row_survives_a_resume(tmp_path):
    driver = _driver(tmp_path, operators={"param_lhs": 1.0}, children=2, generations=4, space={"x": (0.0, 1.0)})
    first = [driver._next_lhs_row(driver._seed_candidate()) for _ in range(3)]
    state = _lhs_state(tmp_path)
    assert state["cubes"]["typed"]["next"] == 3 and state["cubes"]["typed"]["K"] == 8
    again = _driver(tmp_path, operators={"param_lhs": 1.0}, children=2, generations=4, space={"x": (0.0, 1.0)})
    assert again._next_lhs_row(again._seed_candidate()) == state["cubes"]["typed"]["rows"][3]
    assert first == state["cubes"]["typed"]["rows"][:3]


def test_a_new_cube_is_built_when_the_rows_run_out(tmp_path):
    driver = _driver(tmp_path, operators={"param_lhs": 0.5, "param_local": 0.5}, children=4, generations=2,
                     space={"x": (0.0, 1.0), "y": (0.0, 1.0)})
    first = [driver._next_lhs_row(driver._seed_candidate()) for _ in range(4)]
    assert _lhs_state(tmp_path)["cubes"]["typed"]["built"] == 1
    fifth = driver._next_lhs_row(driver._seed_candidate())
    cube = _lhs_state(tmp_path)["cubes"]["typed"]
    assert cube["built"] == 2 and cube["next"] == 1 and cube["rows"][0] == fifth
    assert fifth not in first


def test_a_param_lhs_child_draws_one_number_from_the_run_per_cube(tmp_path):
    """The run's generator gives one draw per cube and nothing per child."""
    driver = _driver(tmp_path, operators={"param_lhs": 1.0}, children=2, generations=4, space={"x": (0.0, 1.0)})
    driver._next_lhs_row(driver._seed_candidate())
    after_build = driver.rng.getstate()
    for _ in range(5):
        driver._invoke("param_lhs", driver._seed_candidate(), [], "none", None, "g001-c0009")
    assert driver.rng.getstate() == after_build


def test_a_reprompted_lhs_child_takes_the_next_row(tmp_path):
    """A child whose row is a configuration the run already holds is a
    duplicate; its one re-prompt takes the next row of the cube."""
    driver = _driver(tmp_path, operators={"param_lhs": 1.0}, children=4, generations=4,
                     space={"x": (0.0, 1.0), "y": (0.0, 1.0)})
    space = driver.config.problem.parameters
    seed = driver._seed_candidate()
    driver._next_lhs_row(seed)  # row 0, handed out earlier
    rows = _lhs_state(tmp_path)["cubes"]["typed"]["rows"]
    driver.novelty.add("g001-c0050", space.render_block(rows[1]), parent_block=seed.block)
    child = driver._breed_one(2, "param_lhs", seed)
    assert not child.rejected and child.block == space.render_block(rows[2])
    assert _lhs_state(tmp_path)["cubes"]["typed"]["next"] == 3
    assert driver._taken == [rows[2]]


def test_a_row_that_breaks_a_constraint_is_passed_over(tmp_path):
    driver = _driver(tmp_path, operators={"param_lhs": 1.0}, children=4, generations=4,
                     space={"x": (0.0, 1.0), "y": (0.0, 1.0)}, constraints=["x <= y"])
    space = driver.config.problem.parameters
    handed = [driver._next_lhs_row(driver._seed_candidate()) for _ in range(6)]
    cube = _lhs_state(tmp_path)["cubes"]["typed"]
    assert all(space.satisfies(row) for row in handed)
    assert cube["next"] > 6, "some rows broke the constraint and were passed over"
    assert handed == [row for row in cube["rows"][: cube["next"]] if space.satisfies(row)]


def test_a_row_equal_to_the_parent_is_passed_over(tmp_path):
    driver = _driver(tmp_path, operators={"param_lhs": 1.0}, children=4, generations=4,
                     space={"x": (0.0, 1.0), "y": (0.0, 1.0)})
    space = driver.config.problem.parameters
    driver._next_lhs_row(driver._seed_candidate())
    rows = _lhs_state(tmp_path)["cubes"]["typed"]["rows"]
    parent = _candidate(space, "g001-c0002", dict(rows[1]))
    assert driver._next_lhs_row(parent) == rows[2]
    assert _lhs_state(tmp_path)["cubes"]["typed"]["next"] == 3


def test_a_space_of_one_point_says_so_and_builds_no_extra_cube(tmp_path, monkeypatch):
    """Every row equal to the parent (a space the parser cannot declare, so the
    cube is made so): the child fails as it did before, and no second cube is built."""
    import evolvekit.search.driver as driver_module

    driver = _driver(tmp_path, operators={"param_lhs": 1.0}, children=2, generations=2, space={"x": (0.0, 1.0)})
    defaults = driver.config.problem.parameters.defaults()
    monkeypatch.setattr(driver_module, "latin_rows", lambda space, count, rng: [(dict(defaults), [])] * count)
    result = driver._invoke("param_lhs", driver._seed_candidate(), [], "none", None, "g001-c0002")
    assert not result.ok and "every sample equals the parent" in result.error
    cube = _lhs_state(tmp_path)["cubes"]["typed"]
    assert cube["built"] == 1 and cube["next"] == cube["K"] == 4


def test_a_cube_is_built_anew_when_the_space_changes_and_kept_when_only_the_size_does(tmp_path):
    """A resumed run whose space is declared differently (here a wider range)
    does not take the old cube's rows; one whose run is only longer does."""
    driver = _driver(tmp_path, operators={"param_lhs": 1.0}, children=2, generations=4, space={"x": (0.0, 1.0)})
    driver._next_lhs_row(driver._seed_candidate())
    old = _lhs_state(tmp_path)["cubes"]["typed"]
    longer = _driver(tmp_path, operators={"param_lhs": 1.0}, children=4, generations=9, space={"x": (0.0, 1.0)})
    assert longer._next_lhs_row(longer._seed_candidate()) == old["rows"][1]
    assert _lhs_state(tmp_path)["cubes"]["typed"]["K"] == 8, "kept until it is used up"
    wider = _driver(tmp_path, operators={"param_lhs": 1.0}, children=2, generations=4, space={"x": (0.0, 10.0)})
    row = wider._next_lhs_row(wider._seed_candidate())
    cube = _lhs_state(tmp_path)["cubes"]["typed"]
    assert cube["rows"] != old["rows"] and cube["next"] == 1 and row == cube["rows"][0]
    assert any(r["x"] > 1.0 for r in cube["rows"])


def test_a_cube_of_rows_that_all_break_a_constraint_leaves_the_last_to_the_static_stage(tmp_path):
    driver = _driver(tmp_path, operators={"param_lhs": 1.0}, children=2, generations=2,
                     space={"x": (0.0, 1.0)}, constraints=["x == 0.5"])
    row = driver._next_lhs_row(driver._seed_candidate())
    cube = _lhs_state(tmp_path)["cubes"]["typed"]
    assert cube["next"] == cube["K"] == 4 and row == cube["rows"][3]


def test_an_untyped_params_block_gets_a_cube_of_its_own(sweepable_project, tmp_path):  # noqa: F811
    """`# PARAMS` ranges as the key; integers stay integers. A parent whose
    declaration differs gets its own cube."""
    from evolvekit.candidate import Candidate as _Candidate
    from evolvekit.config import load_config
    from evolvekit.search.driver import Driver

    driver = Driver(load_config(sweepable_project), run_dir=tmp_path / "run", log=lambda m: None)
    seed = driver._seed_candidate()
    rows = [driver._next_lhs_row(seed) for _ in range(9)]
    key = 'params:{"ALPHA": [0.0, 10.0], "STEPS": [1.0, 9.0]}'
    cube = _lhs_state(tmp_path)["cubes"][key]
    assert cube["K"] == 9 and cube["rows"] == rows  # generations 3 x children 3 x share 1
    assert all(isinstance(r["STEPS"], int) and isinstance(r["ALPHA"], float) for r in rows)
    assert sorted(int(r["ALPHA"] / 10.0 * 9) for r in rows) == list(range(9))
    outcome = driver._invoke("param_lhs", seed, [], "none", None, "g001-c0002")
    second = _lhs_state(tmp_path)["cubes"][key]
    assert outcome.ok and second["built"] == 2 and outcome.meta["params"] == second["rows"][0]
    assert f"STEPS = {outcome.meta['params']['STEPS']}\n" in outcome.block
    other = _Candidate(id="o", generation=1, block=seed.block.replace('"ALPHA": [0.0, 10.0]', '"ALPHA": [0.0, 20.0]'),
                       source="", operator="rewrite")
    driver._next_lhs_row(other)
    assert set(_lhs_state(tmp_path)["cubes"]) == {key, 'params:{"ALPHA": [0.0, 20.0], "STEPS": [1.0, 9.0]}'}


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
