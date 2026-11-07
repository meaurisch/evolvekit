"""`evaluate.score.levels`: goals in order of importance.

"First the most deliveries per hour, two plans within 1 % counting as equal;
then the least waiting." The levels compile to one finite scalar anchored at
the seed, so every consumer of `score` -- ranking, the archive, promotion,
patience, the estimator -- keeps working unchanged.
"""

from __future__ import annotations

import json
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
from evolvekit.confirm import BASELINE, Comparison, _analyse, confirm, render_markdown  # noqa: E402

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


def test_a_level_that_does_not_differ_clearly_is_not_confirmed():
    comparison = _comparison(dph_factor=1.0, wait_shift=0.01)
    _analyse(comparison)
    levels = comparison.per_candidate["g003-c0014"]["levels"]
    assert levels["verdict"].startswith("equal on level 1 (dph within 1 %), worse on level 2 (wait)")
    assert levels["confirmed"] is False


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