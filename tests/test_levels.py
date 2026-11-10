"""`evaluate.score.levels`: goals in order of importance.

"First the most deliveries per hour, two plans within 1 % counting as equal;
then the least waiting." The levels compile to one finite scalar anchored at
the seed, so every consumer of `score` -- ranking, the archive, promotion,
patience, the estimator -- keeps working unchanged.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from evolvekit.config import ConfigError, LevelConfig, build_config
from evolvekit.evaluate.levels import BASE, K, Anchor, level_score, level_steps, make_anchor
from evolvekit.search.driver import Driver

TWO = (LevelConfig("dph", "maximize", 0.01, True), LevelConfig("wait", "minimize"))


# -- config -----------------------------------------------------------------


def _raw(score=None, *, stages=None, penalties=None, stop=None):
    evaluate = {
        "stages": stages
        or [
            {"id": "static", "kind": "builtin-static"},
            {"id": "full", "kind": "command", "kpis_from": "stdout", "command": "{python} solver.py {params}"},
        ],
        "score": score
        or {
            "levels": [
                {"kpi": "dph", "direction": "maximize", "tolerance": 0.01, "relative": True},
                {"kpi": "wait", "direction": "minimize"},
            ]
        },
    }
    if penalties is not None:
        evaluate["penalties"] = penalties
    raw = {
        "problem": {"parameters": {"x": {"type": "float", "low": 0.0, "high": 1.0, "default": 0.5}}},
        "evaluate": evaluate,
        "search": {"operators": {"param_lhs": 1.0}},
    }
    if stop is not None:
        raw["stop"] = stop
    return raw


def test_levels_name_the_objective_and_are_required_kpis(tmp_path):
    config = build_config(_raw(), base_dir=tmp_path)
    score = config.evaluate.score
    assert (score.objective, score.direction) == ("dph", "maximize")
    assert score.levels == TWO
    assert config.evaluate.required_kpis == ("dph", "wait")


def _levels(*levels):
    return {"levels": list(levels)}


LEVEL_1 = {"kpi": "dph", "direction": "maximize", "tolerance": 0.01, "relative": True}
LEVEL_2 = {"kpi": "wait", "direction": "minimize"}
FANOUT = {"id": "full", "kind": "command", "kpis_from": "stdout", "command": "{python} solver.py {instance} {params}",
          "instances": ["a", "b", "c"]}


@pytest.mark.parametrize(
    "raw, message",
    [
        (_raw(_levels(LEVEL_1)), r"evaluate\.score\.levels: needs at least two levels"),
        (_raw(_levels(*[{"kpi": f"k{i}", "direction": "maximize", "tolerance": 1} for i in range(4)], LEVEL_2)),
         r"evaluate\.score\.levels: at most 4 levels"),
        (_raw(_levels(LEVEL_1, {"kpi": "dph", "direction": "minimize"})), r"evaluate\.score\.levels: .*'dph' more than once"),
        (_raw(_levels({"kpi": "dph", "tolerance": 1}, LEVEL_2)), r"evaluate\.score\.levels\[0\]\.direction: required"),
        (_raw(_levels({**LEVEL_1, "direction": "up"}, LEVEL_2)), r"evaluate\.score\.levels\[0\]\.direction: must be"),
        (_raw(_levels({"kpi": "dph", "direction": "maximize"}, LEVEL_2)),
         r"evaluate\.score\.levels\[0\]\.tolerance: required on every level but the last"),
        (_raw(_levels(LEVEL_1, {**LEVEL_2, "tolerance": 1})), r"evaluate\.score\.levels\[1\]\.tolerance: the last level has none"),
        (_raw(_levels({**LEVEL_1, "tolerance": 0}, LEVEL_2)), r"evaluate\.score\.levels\[0\]\.tolerance: must be > 0"),
        (_raw(_levels(LEVEL_1, {**LEVEL_2, "relative": True})), r"evaluate\.score\.levels\[1\]\.relative: only with a tolerance"),
        (_raw(_levels(LEVEL_1, {**LEVEL_2, "weight": 2})), r"evaluate\.score\.levels\[1\]: unknown key"),
        (_raw({**_levels(LEVEL_1, LEVEL_2), "objective": "wait"}), r"evaluate\.score\.objective: .*level 1"),
        (_raw({**_levels(LEVEL_1, LEVEL_2), "direction": "minimize"}), r"evaluate\.score\.direction: .*level 1"),
        (_raw({**_levels(LEVEL_1, LEVEL_2), "weights": {"dph": 1, "wait": 1}}),
         r"evaluate\.score\.weights: cannot be combined with `levels`"),
        (_raw(penalties=[{"kpi": "wait"}]), r"evaluate\.penalties: cannot be combined with evaluate\.score\.levels"),
        (_raw(stop={"target": 5}), r"stop\.target: cannot be combined with evaluate\.score\.levels"),
        (_raw(stages=[{"id": "static", "kind": "builtin-static"}, {**FANOUT, "race": {"after": 1}}]),
         r"evaluate\.stages\[1\]\.race: cannot be combined with evaluate\.score\.levels"),
        (_raw(stages=[{"id": "static", "kind": "builtin-static"}, {**FANOUT, "normalize": "baseline"}]),
         r"evaluate\.stages\[1\]\.normalize: .*`none`"),
    ],
)
def test_levels_that_cannot_work_are_a_config_error(tmp_path, raw, message):
    with pytest.raises(ConfigError, match=message):
        build_config(raw, base_dir=tmp_path)


def test_a_stage_that_runs_per_instance_compares_plain_means_under_levels(tmp_path):
    config = build_config(_raw(stages=[{"id": "static", "kind": "builtin-static"}, FANOUT]), base_dir=tmp_path)
    assert config.evaluate.stages[1].normalize == "none"


# -- the scalar ----------------------------------------------------------------


def test_the_seed_scores_exactly_zero():
    anchor = make_anchor(TWO, {"dph": 10.0, "wait": 5.0})
    assert level_score(TWO, anchor, {"dph": 10.0, "wait": 5.0}) == 0.0
    assert anchor.tolerances == {"dph": pytest.approx(0.1)} and anchor.scale == pytest.approx(0.5)


def test_one_step_on_a_higher_level_outweighs_anything_below_it():
    anchor = make_anchor(TWO, {"dph": 10.0, "wait": 5.0})
    better_first = level_score(TWO, anchor, {"dph": 10.1, "wait": 5000.0})
    better_second = level_score(TWO, anchor, {"dph": 10.0, "wait": 0.0})
    assert better_first > better_second > 0


def test_within_half_a_step_the_next_level_decides():
    anchor = make_anchor(TWO, {"dph": 10.0, "wait": 5.0})
    assert level_steps(TWO, anchor, {"dph": 10.04, "wait": 5.0}) == [0]
    assert level_score(TWO, anchor, {"dph": 9.97, "wait": 4.0}) > level_score(TWO, anchor, {"dph": 10.04, "wait": 6.0})


def test_steps_saturate_at_k():
    anchor = make_anchor(TWO, {"dph": 10.0, "wait": 5.0})
    assert level_steps(TWO, anchor, {"dph": 1e12, "wait": 5.0}) == [K]
    assert level_steps(TWO, anchor, {"dph": -1e12, "wait": 5.0}) == [-K]


def test_four_levels_keep_exact_steps_and_a_readable_last_level():
    four = tuple(LevelConfig(k, "maximize", 1.0) for k in "abc") + (LevelConfig("d", "minimize"),)
    anchor = make_anchor(four, {"a": 0.0, "b": 0.0, "c": 0.0, "d": 0.0})
    assert anchor.scale == 1.0, "a last level anchored at 0 is scaled by 1"
    top = level_score(four, anchor, {"a": K, "b": 0.0, "c": 0.0, "d": 0.0})
    assert top == K * BASE**2, "the integer part is exact"
    # The extreme: one step better on level 1 against K steps better on every
    # level below and a last level so far ahead that its tanh is exactly 1.0.
    assert level_score(four, anchor, {"a": K, "b": -K, "c": -K, "d": 1e9}) > level_score(
        four, anchor, {"a": K - 1, "b": K, "c": K, "d": -1e9}
    )
    near = {"a": K, "b": K, "c": K}
    assert level_score(four, anchor, {**near, "d": 0.0}) > level_score(four, anchor, {**near, "d": 0.01})


def test_a_relative_tolerance_of_a_zero_anchor_is_taken_as_absolute():
    anchor = make_anchor(TWO, {"dph": 0.0, "wait": 5.0})
    assert anchor.tolerances == {"dph": 0.01}


def test_an_anchor_round_trips_through_json():
    anchor = make_anchor(TWO, {"dph": 10.0, "wait": 5.0})
    assert Anchor.from_json(json.loads(json.dumps(anchor.to_json()))) == anchor


def test_the_last_level_is_scaled_by_the_seeds_own_range_over_instances():
    anchor = make_anchor(TWO, {"dph": 10.0, "wait": 100.0}, per_instance={"wait": [50.0, 100.0, 150.0]})
    assert anchor.scale == pytest.approx(100.0)
    forty, thirty = (level_score(TWO, anchor, {"dph": 10.0, "wait": 100.0 * (1 - g)}) for g in (0.4, 0.3))
    assert forty - thirty > 0.04, "a 40 % and a 30 % gain are different numbers (0.002 apart at a tenth)"
    narrow = make_anchor(TWO, {"dph": 10.0, "wait": 100.0}, per_instance={"wait": [99.0, 101.0]})
    assert narrow.scale == pytest.approx(10.0), "never below a tenth of the seed's value"
    assert make_anchor(TWO, {"dph": 10.0, "wait": 5.0}).scale == pytest.approx(0.5), "without instances, as before"
    assert make_anchor(TWO, {"dph": 10.0, "wait": 5.0}, per_instance={"wait": [5.0]}).scale == pytest.approx(0.5), (
        "one instance has no range"
    )
    assert make_anchor(TWO, {"dph": 10.0, "wait": 0.0}, per_instance={"wait": [0.0, 0.0]}).scale == 1.0, (
        "no value and no range: scaled by 1"
    )


# -- a run -------------------------------------------------------------------

# Level 1 (deliveries per hour) barely moves -- every configuration is within
# half a step of the seed's -- so level 2 (waiting) decides, and the best by
# level 1 alone (x = 1) is not the best of the run.
SOLVER = (
    "import argparse, json\n"
    "p = argparse.ArgumentParser(); p.add_argument('--x', type=float)\n"
    "x = p.parse_args().x\n"
    "print(json.dumps({'dph': 10 + 0.4 * x, 'wait': (x - 0.2) ** 2}))\n"
)


def _levels_config(tmp_path: Path, **search):
    (tmp_path / "solver.py").write_text(SOLVER, encoding="utf-8")
    raw = _raw(_levels({"kpi": "dph", "direction": "maximize", "tolerance": 1.0}, LEVEL_2))
    raw["search"] = {"operators": {"param_lhs": 1.0}, "children_per_generation": 6, "generations": 2, "seed": 5, **search}
    raw["stop"] = {"patience": 20}
    return build_config(raw, base_dir=tmp_path)


@pytest.mark.slow
def test_the_run_ranks_by_the_levels_in_order(tmp_path):
    config = _levels_config(tmp_path)
    driver = Driver(config, run_dir=tmp_path / "run")
    summary = driver.run()
    competing = [r for r in driver.ledger.runs() if r["competes"]]
    assert summary.best.kpis["wait"] == min(r["kpis"]["wait"] for r in competing)
    assert summary.best.kpis["dph"] < max(r["kpis"]["dph"] for r in competing), "level 1 alone would pick another"
    anchors = json.loads((tmp_path / "run" / "work" / "levels.json").read_text(encoding="utf-8"))
    assert anchors["full"]["values"] == {"dph": pytest.approx(10.2), "wait": pytest.approx(0.09)}


@pytest.mark.slow
def test_a_resumed_run_scores_against_the_same_anchor(tmp_path):
    Driver(_levels_config(tmp_path, generations=1), run_dir=tmp_path / "run").run()
    driver = Driver(_levels_config(tmp_path, generations=2), run_dir=tmp_path / "run")
    driver.run()
    anchors = json.loads((tmp_path / "run" / "work" / "levels.json").read_text(encoding="utf-8"))
    anchor = Anchor.from_json(anchors["full"])
    levels = driver.config.evaluate.score.levels
    rows = [r for r in driver.ledger.runs() if r["competes"]]
    assert {r["generation"] for r in rows} >= {0, 1, 2}
    for row in rows:
        assert row["score"] == pytest.approx(level_score(levels, anchor, row["kpis"]))


@pytest.mark.slow
def test_a_run_directory_remembers_its_levels(tmp_path):
    Driver(_levels_config(tmp_path, generations=1), run_dir=tmp_path / "run").run()
    (tmp_path / "solver.py").write_text(SOLVER, encoding="utf-8")
    raw = _raw(_levels({"kpi": "dph", "direction": "maximize", "tolerance": 2.0}, LEVEL_2))
    raw["search"] = {"operators": {"param_lhs": 1.0}, "generations": 2}
    with pytest.raises(ValueError, match="the levels changed"):
        Driver(build_config(raw, base_dir=tmp_path), run_dir=tmp_path / "run").run()


FANOUT_SOLVER = (
    "import argparse, json, sys\n"
    "p = argparse.ArgumentParser(); p.add_argument('instance'); p.add_argument('--x', type=float)\n"
    "a = p.parse_args()\n"
    "size = {'a': 50.0, 'b': 100.0, 'c': 150.0}[a.instance]\n"
    "print(json.dumps({'dph': 10 + 0.01 * a.x, 'wait': size * (1 + a.x)}))\n"
)


@pytest.mark.slow
def test_a_run_anchors_its_last_level_on_the_seeds_spread_over_instances(tmp_path):
    (tmp_path / "solver.py").write_text(FANOUT_SOLVER, encoding="utf-8")
    raw = _raw(stages=[{"id": "static", "kind": "builtin-static"}, FANOUT])
    raw["search"] = {"operators": {"param_lhs": 1.0}, "children_per_generation": 2, "generations": 1, "seed": 5}
    Driver(build_config(raw, base_dir=tmp_path), run_dir=tmp_path / "run").run()
    anchors = json.loads((tmp_path / "run" / "work" / "levels.json").read_text(encoding="utf-8"))
    assert anchors["full"]["scale"] == pytest.approx(150.0), "the seed (x = 0.5): 75, 150, 225"


# -- status and the dashboard -------------------------------------------------

from evolvekit import dashboard  # noqa: E402
from evolvekit.status import build_status, render_text  # noqa: E402


@pytest.mark.slow
def test_status_reports_progress_level_by_level(tmp_path):
    Driver(_levels_config(tmp_path), run_dir=tmp_path / "run").run()
    document = build_status(tmp_path / "run")
    levels = document["progress"]["levels"]
    assert levels["available"] is True
    first, second = levels["levels"]
    assert (first["kpi"], first["state"], first["steps"], first["tolerance_abs"]) == ("dph", "equal", 0, 1.0)
    assert (second["kpi"], second["state"]) == ("wait", "better") and second["difference"] < 0
    assert second["steps"] is None and second["tolerance"] is None, "the last level has no steps"
    assert levels["deciding"] == 2
    assert levels["verdict"].startswith("equal on level 1 (dph, within 1), better on level 2 (wait, ")
    assert levels["saturated"] == [] and levels["warning"] is None
    text = render_text(document)
    assert "levels       : equal on level 1" in text
    assert "  level 1    : dph (maximize)" in text and "  level 2    : wait (minimize)" in text


def _event(seq, type, **fields):
    return {"seq": seq, "ts": f"2026-09-26T10:00:0{seq}.000+00:00", "session": "s1", "pid": 1, "type": type, **fields}


def test_status_warns_about_a_candidate_beyond_k_steps(tmp_path):
    run = tmp_path / "run"
    (run / "work").mkdir(parents=True)
    levels = [{"kpi": "dph", "direction": "maximize", "tolerance": 0.01, "relative": False},
              {"kpi": "wait", "direction": "minimize", "tolerance": None, "relative": False}]
    events = [
        _event(1, "run_started", objective="dph", direction="maximize", levels=levels,
               stages=[{"id": "full", "kind": "command", "final": True}]),
        _event(2, "run_finished", stop_reason="generations exhausted"),
    ]
    rows = [
        {"id": "g000-c0001", "generation": 0, "operator": "human-seed", "competes": True, "score": 0.0,
         "kpis": {"dph": 10.0, "wait": 5.0}, "block": ""},
        {"id": "g001-c0002", "generation": 1, "operator": "param_lhs", "competes": True, "score": 1e8,
         "kpis": {"dph": 500.0, "wait": 5.0}, "block": ""},
    ]
    (run / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    (run / "runs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    anchor = make_anchor((LevelConfig("dph", "maximize", 0.01), LevelConfig("wait", "minimize")), {"dph": 10.0, "wait": 5.0})
    (run / "work" / "levels.json").write_text(json.dumps({"full": anchor.to_json()}), encoding="utf-8")
    summary = build_status(run)["progress"]["levels"]
    assert summary["saturated"] == ["g001-c0002"]
    assert "10,000" in summary["warning"]


def test_status_says_when_a_run_does_not_rank_by_levels(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    events = [_event(1, "run_started", objective="cost", direction="minimize",
                     stages=[{"id": "full", "kind": "command", "final": True}])]
    (run / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    levels = build_status(run)["progress"]["levels"]
    assert levels["available"] is False and levels["levels"] == [] and "evaluate.score.levels" in levels["why"]


def test_the_dashboard_draws_a_row_per_level():
    page = (Path(dashboard.__file__).parent / "index.html").read_text(encoding="utf-8")
    assert "pr.levels" in page and '"counts as equal within"' in page and 'id: "levels"' in page

# -- confirm, level by level ---------------------------------------------------

import random as _random  # noqa: E402

from evolvekit import cli  # noqa: E402
from evolvekit.confirm import (  # noqa: E402
    BASELINE, Comparison, _analyse, confirm, decision_of, render_markdown, verdict_summary,
)
from evolvekit.stats import _t90, log_gain, t_cdf  # noqa: E402

DESCRIBED = [LevelConfig("dph", "maximize", 0.01, True).describe(), LevelConfig("wait", "minimize").describe()]


def _comparison(dph_factor: float, wait_shift: float) -> Comparison:
    """Three instances, two seeds: the candidate's deliveries per hour are the
    baseline's times `dph_factor`, its waiting the baseline's plus `wait_shift`."""
    rng = _random.Random(4)
    comparison = Comparison(
        label="t", objective="dph", direction="maximize", stage="full", seeds=[1, 2],
        instances=["a", "b", "c"], baseline_id="g000-c0001", candidates=["g003-c0014"], levels=DESCRIBED,
    )
    for instance, size in (("a", 10.0), ("b", 20.0), ("c", 40.0)):
        for seed in (1, 2):
            dph, wait = size * (1 + rng.uniform(-0.001, 0.001)), 5.0 + rng.uniform(-0.05, 0.05)
            comparison.runs.append({"configuration": BASELINE, "instance": instance, "seed": seed, "ok": True,
                                    "cached": False, "kpis": {"dph": dph, "wait": wait}, "failure": None})
            comparison.runs.append({"configuration": "g003-c0014", "instance": instance, "seed": seed, "ok": True,
                                    "cached": False, "kpis": {"dph": dph * dph_factor, "wait": wait + wait_shift + rng.uniform(-0.05, 0.05)},
                                    "failure": None})
    return comparison


def test_confirm_says_equal_on_level_1_and_better_on_level_2():
    comparison = _comparison(dph_factor=1.002, wait_shift=-1.0)
    _analyse(comparison)
    levels = comparison.per_candidate["g003-c0014"]["levels"]
    assert levels["verdict"] == "equal on level 1 (dph within 1 %), better on level 2 (wait): clear"
    assert levels["deciding"] == 2 and levels["confirmed"] is True
    first, second = levels["levels"]
    assert first["state"] == "equal" and first["tolerance_abs"] == pytest.approx(0.01 * first["baseline_mean"])
    assert second["state"] == "better" and second["clear"] and second["mean_difference"] > 0, "positive is better"
    markdown = render_markdown(comparison)
    assert "### Level by level" in markdown and "equal on level 1 (dph within 1 %)" in markdown.lower()


def test_confirm_says_worse_when_level_1_falls_beyond_its_tolerance():
    comparison = _comparison(dph_factor=0.95, wait_shift=-1.0)
    _analyse(comparison)
    levels = comparison.per_candidate["g003-c0014"]["levels"]
    assert levels["verdict"] == "worse on level 1 (dph): clear"
    assert levels["deciding"] == 1 and levels["confirmed"] is False
    assert levels["decision"] == "worse" and decision_of(comparison.per_candidate["g003-c0014"]) == "worse"


def test_a_level_that_does_not_differ_clearly_is_not_confirmed():
    comparison = _comparison(dph_factor=1.0, wait_shift=0.01)
    _analyse(comparison)
    levels = comparison.per_candidate["g003-c0014"]["levels"]
    # Worse in sign, but its chance of a gain is above 5 %: worse is as strict as use (decision 14b).
    assert 0.05 < levels["levels"][1]["chance"] <= 0.10
    assert levels["verdict"] == "equal on level 1 (dph within 1 %), level 2 (wait) is not proven either way"
    assert levels["confirmed"] is False and levels["decision"] == "keep"


def test_a_level_whose_interval_straddles_its_tolerance_is_not_yet_decided():
    """About 1 % more deliveries per hour, give or take: the interval crosses the 1 % edge.
    (`_comparison` gives the candidate's dph no noise of its own, so it cannot straddle.)"""
    rng = _random.Random(8)
    comparison = Comparison(label="t", objective="dph", direction="maximize", stage="full", seeds=[1, 2],
                            instances=["a", "b", "c"], baseline_id="g000-c0001", candidates=["c"], levels=DESCRIBED)
    for instance, size in (("a", 10.0), ("b", 20.0), ("c", 40.0)):
        for seed in (1, 2):
            comparison.runs += [
                {"configuration": BASELINE, "instance": instance, "seed": seed, "ok": True,
                 "kpis": {"dph": size, "wait": 5.0}},
                {"configuration": "c", "instance": instance, "seed": seed, "ok": True,
                 "kpis": {"dph": size * (1.01 + rng.uniform(-0.004, 0.004)), "wait": 4.0}},
            ]
    _analyse(comparison)
    levels = comparison.per_candidate["c"]["levels"]
    assert levels["levels"][0]["ci95"][0] < 1.0 < levels["levels"][0]["ci95"][1], "the fixture straddles the edge"
    assert levels["levels"][0]["state"] == "undecided"
    assert levels["verdict"] == "level 1 (dph) is not yet decided within 1 %"
    assert levels["deciding"] == 1 and levels["confirmed"] is False and levels["decision"] == "keep"
    assert decision_of(comparison.per_candidate["c"]) == "keep", "level 2 is better, and does not speak"
    assert levels["levels"][1]["state"] == "better", "below the deciding level: reported, not judged"
    assert "not yet decided" in render_markdown(comparison)


def test_a_level_with_one_case_is_not_yet_decided():
    comparison = _comparison(dph_factor=1.0, wait_shift=-1.0)
    comparison.instances = ["a"]
    comparison.runs = [r for r in comparison.runs if r["instance"] == "a"]
    _analyse(comparison)
    levels = comparison.per_candidate["g003-c0014"]["levels"]
    assert levels["levels"][0]["ci95"] is None and levels["levels"][0]["state"] == "undecided"
    assert levels["deciding"] == 1 and levels["decision"] == "keep"


def _edge(rng: _random.Random, factor: float) -> Comparison:
    """Four cases, two runs each, log-normal noise; the candidate's true dph is `factor` times the
    starting point's on every case."""
    comparison = Comparison(label="t", objective="dph", direction="maximize", stage="full", seeds=[1, 2],
                            instances=["a", "b", "c", "d"], baseline_id="g000-c0001", candidates=["c"],
                            levels=DESCRIBED)
    for instance, size in (("a", 10.0), ("b", 20.0), ("c", 40.0), ("d", 80.0)):
        for seed in (1, 2):
            dph = size * math.exp(rng.gauss(0, 0.004))
            comparison.runs += [
                {"configuration": BASELINE, "instance": instance, "seed": seed, "ok": True,
                 "kpis": {"dph": dph, "wait": 5.0}},
                {"configuration": "c", "instance": instance, "seed": seed, "ok": True,
                 "kpis": {"dph": dph * factor * math.exp(rng.gauss(0, 0.004)), "wait": 5.0}},
            ]
    return comparison


def test_a_level_at_its_tolerance_edge_is_called_equal_at_most_one_in_forty_per_side():
    """Two one-sided tests on the 95 % interval: a true difference exactly at the 1 % tolerance, on
    either side, is called equal about 1 in 40 of the time (2.5 %). 4000 seeded comparisons per side
    (a standard error of 0.25 %); the bound 3.5 % is four of them above the nominal rate."""
    rng = _random.Random(15)
    studies = 4000
    for factor in (1.01, 0.99):
        equal = 0
        for _ in range(studies):
            comparison = _edge(rng, factor)
            _analyse(comparison)
            equal += comparison.per_candidate["c"]["levels"]["levels"][0]["state"] == "equal"
        rate = equal / studies
        print(f"true difference at the {'upper' if factor > 1 else 'lower'} edge: called equal {rate:.2%}")
        assert rate < 0.035, f"{equal} of {studies} at {factor}"


def test_a_level_that_decides_is_read_by_the_rule_that_decides_without_levels():
    """Level 1 equal, level 2 decides: its decision is the posterior rule's on its own log gains,
    the same `use` with and without levels (decision 6)."""
    rng = _random.Random(23)
    seen = set()
    for shift in (-0.6, -0.3, -0.15, 0.0, 0.15, 0.3):
        for _ in range(20):
            comparison = Comparison(label="t", objective="dph", direction="maximize", stage="full", seeds=[1],
                                    instances=["a", "b", "c"], baseline_id="g000-c0001", candidates=["c"],
                                    levels=DESCRIBED)
            gains = []
            for instance in ("a", "b", "c"):
                wait, mine = 5.0, 5.0 + shift + rng.gauss(0, 0.2)
                gains.append(log_gain(wait, mine, "minimize"))
                comparison.runs += [
                    {"configuration": BASELINE, "instance": instance, "seed": 1, "ok": True,
                     "kpis": {"dph": 10.0, "wait": wait}},
                    {"configuration": "c", "instance": instance, "seed": 1, "ok": True,
                     "kpis": {"dph": 10.0, "wait": mine}},
                ]
            _analyse(comparison)
            result = comparison.per_candidate["c"]
            expected = verdict_summary(gains, direction="minimize")["decision"]
            assert result["levels"]["deciding"] == 2
            assert result["levels"]["decision"] == expected and decision_of(result) == expected
            assert result["levels"]["confirmed"] is (expected == "use")
            assert result["levels"]["levels"][1]["clear"] is (expected in ("use", "worse")), "the words follow it"
            seen.add(expected)
    assert seen == {"use", "keep", "worse"}, "the fixture reaches every answer"


PER_INSTANCE_SOLVER = (
    "import argparse, json\n"
    "p = argparse.ArgumentParser(); p.add_argument('--x', type=float); p.add_argument('--instance'); p.add_argument('--seed', type=int)\n"
    "a = p.parse_args()\n"
    "size = {'a': 1.0, 'b': 2.0}[a.instance]\n"
    "print(json.dumps({'dph': size * (10 + 0.4 * a.x), 'wait': size * (a.x - 0.2) ** 2 + 0.001 * a.seed}))\n"
)


@pytest.mark.slow
def test_confirm_compares_a_levels_run_level_by_level(tmp_path):
    (tmp_path / "solver.py").write_text(PER_INSTANCE_SOLVER, encoding="utf-8")
    raw = _raw(
        _levels({"kpi": "dph", "direction": "maximize", "tolerance": 0.05, "relative": True}, LEVEL_2),
        stages=[{"id": "static", "kind": "builtin-static"},
                {"id": "full", "kind": "command", "kpis_from": "stdout", "instances": ["a", "b"],
                 "command": "{python} solver.py --instance {instance} --seed {seed} {params}"}],
    )
    raw["search"] = {"operators": {"param_lhs": 1.0}, "children_per_generation": 6, "generations": 1, "seed": 5}
    config_path = tmp_path / "evolvekit.yaml"
    config_path.write_text(json.dumps(raw), encoding="utf-8")
    config = build_config(raw, base_dir=tmp_path, source=config_path)
    Driver(config, run_dir=tmp_path / "run").run()
    comparison = confirm(config, tmp_path / "run", seeds=[101, 102], label="t", log=lambda _m: None)
    written = json.loads((tmp_path / "run" / "confirm" / "t" / "comparison.json").read_text(encoding="utf-8"))
    assert [level["kpi"] for level in written["levels"]] == ["dph", "wait"]
    assert written["levels"][0]["tolerance"] == 0.05 and written["levels"][0]["relative"] is True
    (best,) = comparison.candidates
    result = written["per_candidate"][best]["levels"]
    assert [row["kpi"] for row in result["levels"]] == ["dph", "wait"]
    code = cli.main(["confirm", "--config", str(config_path), "--run-dir", str(tmp_path / "run"),
                     "--seeds", "101,102", "--label", "t"])
    assert code == (0 if result["confirmed"] else 1)

def test_a_level_with_zeros_in_the_baseline_is_compared_in_its_own_units():
    comparison = Comparison(
        label="t", objective="dph", direction="maximize", stage="full", seeds=[1, 2], instances=["a", "b", "c"],
        baseline_id="g000-c0001", candidates=["c1"],
        levels=[DESCRIBED[0], LevelConfig("late", "minimize").describe()],
    )
    for instance, late_before, late_after in (("a", 0, 0), ("b", 2, 1), ("c", 4, 2)):
        for seed in (1, 2):
            for name, late in ((BASELINE, late_before), ("c1", late_after)):
                comparison.runs.append({"configuration": name, "instance": instance, "seed": seed, "ok": True,
                                        "cached": False, "kpis": {"dph": 10.0, "late": float(late)}, "failure": None})
    _analyse(comparison)
    first, second = comparison.per_candidate["c1"]["levels"]["levels"]
    assert first["unit"] == "percent" and first["state"] == "equal"
    assert second["unit"] == "absolute" and second["mean_difference"] == pytest.approx(1.0)
    assert second["state"] == "better"
    # The same posterior in the KPI's own units: per-case gains 0, 1, 2 (mean 1, scale 1/sqrt(3), 2 df).
    assert second["chance"] == pytest.approx(t_cdf(math.sqrt(3), 2))
    assert second["worst"] == pytest.approx(1 - _t90(2) / math.sqrt(3))
    levels = comparison.per_candidate["c1"]["levels"]
    assert second["chance"] < 0.95 and levels["decision"] == "keep" and second["clear"] is False
    assert levels["verdict"] == "equal on level 1 (dph within 1 %), level 2 (late) is not proven either way"


def test_a_last_level_the_rule_does_not_speak_on_is_not_proven_either_way():
    comparison = Comparison(label="t", objective="dph", direction="maximize", stage="full", seeds=[1],
                            instances=["a", "b", "c"], baseline_id="g000-c0001", candidates=["c"], levels=DESCRIBED)
    for instance, wait in (("a", 4.9), ("b", 5.05), ("c", 4.95)):
        comparison.runs += [
            {"configuration": BASELINE, "instance": instance, "seed": 1, "ok": True, "kpis": {"dph": 10.0, "wait": 5.0}},
            {"configuration": "c", "instance": instance, "seed": 1, "ok": True, "kpis": {"dph": 10.0, "wait": wait}},
        ]
    _analyse(comparison)
    levels = comparison.per_candidate["c"]["levels"]
    second = levels["levels"][1]
    assert second["state"] == "better" and 0.5 < second["chance"] < 0.95 and second["clear"] is False
    assert levels["verdict"] == "equal on level 1 (dph within 1 %), level 2 (wait) is not proven either way"
    assert "not distinguishable" not in levels["verdict"] and levels["decision"] == "keep"
    assert levels["deciding"] == 2 and levels["read_on"] == 2


def _levels_result(decision: str, chance: float) -> dict:
    """A levels result as `_analyse_levels` writes it: level 1 equal, level 2 deciding `decision`."""
    return {"summary": {"n": 4, "chance": 0.5, "worst": -1.0, "posterior": {}},
            "levels": {"decision": decision, "confirmed": decision == "use", "deciding": 2, "read_on": 2,
                       "levels": [{"level": 1, "state": "equal", "chance": 0.5},
                                  {"level": 2, "state": "better", "chance": chance}]}}


def test_top_n_with_levels_pays_for_choosing_among_its_candidates():
    """Fix round 1, I4: levels results join the Holm family on the deciding level's chance."""
    from evolvekit.confirm import apply_holm

    results = {"g1": _levels_result("use", 0.999), "g2": _levels_result("use", 0.96),
               "g3": _levels_result("keep", 0.6)}
    apply_holm(results, what="candidates")
    assert decision_of(results["g1"]) == "use"
    assert decision_of(results["g2"]) == "keep", "0.96 passes alone; second of three, Holm needs 0.975"
    assert results["g2"]["holm"] == {"k": 3, "what": "candidates", "own": "use", "passed": False}
    assert results["g3"]["holm"]["passed"] is False and decision_of(results["g3"]) == "keep"
    mixed = {"lv": _levels_result("use", 0.96), "plain": {"summary": {"n": 4, "chance": 0.96, "worst": 1.0,
                                                                      "posterior": {}}}}
    apply_holm(mixed, what="branches")
    assert mixed["lv"]["holm"]["k"] == 2 and mixed["plain"]["holm"]["k"] == 2, "levels and plain in one family"
    alone = {"g2": _levels_result("use", 0.96)}
    apply_holm(alone, what="candidates")
    assert "holm" not in alone["g2"] and decision_of(alone["g2"]) == "use", "one is no family"
    old = {"a": {"summary": {}, "levels": {"confirmed": True, "deciding": 2, "levels": []}},
           "b": _levels_result("use", 0.96)}
    apply_holm(old, what="branches")
    assert not any("holm" in r for r in old.values()), "a levels result written before A1.5 is no member"


def test_a_bakeoff_with_levels_holds_back_a_branch_holm_does_not_pass():
    """Three branches through `_analyse`: two read "use" on level 2 alone, the weaker one is held back."""
    from evolvekit.confirm import apply_holm

    comparison = Comparison(label="t", objective="dph", direction="maximize", stage="full", seeds=[1],
                            instances=["a", "b", "c", "d"], baseline_id="g000-c0001", candidates=["s", "w", "n"],
                            levels=DESCRIBED)
    waits = {"s": (4.0, 4.1, 3.9, 4.0), "w": (4.6, 4.98, 4.7, 4.85), "n": (5.0, 5.1, 4.9, 5.0)}
    for i, instance in enumerate(("a", "b", "c", "d")):
        comparison.runs.append({"configuration": BASELINE, "instance": instance, "seed": 1, "ok": True,
                                "kpis": {"dph": 10.0, "wait": 5.0}})
        for cid, wait in waits.items():
            comparison.runs.append({"configuration": cid, "instance": instance, "seed": 1, "ok": True,
                                    "kpis": {"dph": 10.0, "wait": wait[i]}})
    _analyse(comparison)
    weak = comparison.per_candidate["w"]["levels"]["levels"][1]["chance"]
    assert 0.95 <= weak < 0.975, f"the fixture: the weaker branch passes alone but not second of three ({weak})"
    assert [decision_of(comparison.per_candidate[c]) for c in "swn"] == ["use", "use", "keep"]
    apply_holm(comparison.per_candidate, what="branches")
    assert [decision_of(comparison.per_candidate[c]) for c in "swn"] == ["use", "keep", "keep"]
    assert "Chosen among 3 branches (Holm): does not pass" in render_markdown(comparison)


def test_whether_runs_can_help_is_read_on_the_level_the_descent_stopped_on():
    """Fix round 1, item 7: level 1 (the objective) has no run noise at all, so on its numbers more runs
    could never help; level 2 decides `keep` and its gains are mostly run noise, so they can."""
    from evolvekit.confirm import runs_can_help, within_share

    rng = _random.Random(31)
    comparison = Comparison(label="t", objective="dph", direction="maximize", stage="full", seeds=[1, 2, 3],
                            instances=["a", "b", "c", "d"], baseline_id="g000-c0001", candidates=["c"],
                            levels=DESCRIBED)
    for instance, size in (("a", 10.0), ("b", 20.0), ("c", 40.0), ("d", 80.0)):
        for seed in (1, 2, 3):
            comparison.runs += [
                {"configuration": BASELINE, "instance": instance, "seed": seed, "ok": True,
                 "kpis": {"dph": size, "wait": 5.0}},
                {"configuration": "c", "instance": instance, "seed": seed, "ok": True,
                 "kpis": {"dph": size, "wait": 5.0 * math.exp(rng.gauss(0, 0.05))}},
            ]
    _analyse(comparison)
    result = comparison.per_candidate["c"]
    assert result["levels"]["read_on"] == 2 and decision_of(result) == "keep"
    assert within_share(result["per_instance"]) == 0.0, "on the objective's gains, runs could not help"
    assert runs_can_help(result) is True, "on level 2's, they can"