"""`confirm`: the run's best against its baseline, paired, on seeds the search
never saw -- and `export`, the winner in a form another program can use."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from evolvekit.cli import main
from evolvekit.config import ConfigError, build_config, load_config
from evolvekit.confirm import (
    confirm,
    decide,
    paired_summary,
    t_cdf,
    wilcoxon_signed_rank,
)
from evolvekit.space import ParameterSpace

# cost = scale * (1 + x) * (1 + noise(instance, seed, x)); `x` is the tuned parameter.
SOLVER = r'''
import argparse, json, random, sys
p = argparse.ArgumentParser()
p.add_argument("--instance"); p.add_argument("--seed", type=int); p.add_argument("--x", type=float)
p.add_argument("--mode", default="std")
a = p.parse_args()
scale = {"small": 100.0, "medium": 1000.0, "large": 10000.0, "fresh": 500.0}[a.instance]
if a.instance == "medium" and a.seed == 1002 and a.x != 0.0:
    sys.exit(7)
noise = random.Random("%s/%d/%s" % (a.instance, a.seed, a.x)).gauss(0, 0.004)
print(json.dumps({"cost": scale * (1.0 + a.x) * (1.0 + noise), "instance": a.instance}))
'''


def _write_config(tmp_path: Path, **stage) -> Path:
    (tmp_path / "solver.py").write_text(SOLVER, encoding="utf-8")
    raw = {
        "problem": {"parameters": {
            "x": {"type": "float", "low": -0.5, "high": 0.5, "default": 0.0},
            "mode": {"type": "choice", "choices": ["std", "deep"], "default": "std", "flag": "--mode"},
        }},
        "evaluate": {
            "stages": [
                {"id": "static", "kind": "builtin-static"},
                {"id": "full", "kind": "command", "kpis_from": "stdout", "timeout": 60,
                 "instances": ["small", "medium", "large"], "workers": 2,
                 "command": "{python} solver.py --instance {instance} --seed {seed} {params}", **stage},
            ],
            "score": {"objective": "cost", "direction": "minimize"},
        },
        "search": {"operators": {"param_lhs": 1.0}},
    }
    import yaml

    path = tmp_path / "evolvekit.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    build_config(raw, base_dir=tmp_path)  # a mistake in this helper should fail here
    return path


def _write_run(tmp_path: Path, config_path: Path, candidates: dict[str, dict]) -> Path:
    """A run directory by hand: the seed, and candidates with a score each."""
    space: ParameterSpace = load_config(config_path).problem.parameters
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    rows = [{"id": "g000-c0001", "generation": 0, "operator": "human-seed", "score": -100.0,
             "params": space.defaults(), "block": space.render_block(space.defaults())}]
    for index, (cid, spec) in enumerate(candidates.items(), start=1):
        params = {**space.defaults(), **spec["params"]}
        rows.append({"id": cid, "generation": index, "operator": "param_local", "score": spec["score"],
                     "params": params, "block": space.render_block(params)})
    with (run_dir / "runs.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps({"rejected": False, "competes": True, "stages_reached": ["static", "full"], **row}) + "\n")
    return run_dir


# -- the statistics --------------------------------------------------------


def test_the_wilcoxon_test_is_exact():
    # All ten differences positive: only 2 of 2^10 sign assignments are this extreme.
    result = wilcoxon_signed_rank([0.5, 1.2, 0.3, 2.0, 0.9, 1.1, 0.7, 0.4, 1.6, 0.8])
    assert result["n"] == 10 and result["w_plus"] == 55.0 and result["p"] == pytest.approx(2 / 1024)
    # n = 6, W- = 6: fourteen of the 64 subsets of {1..6} sum to 6 or less, so p = 2 * 14 / 64.
    assert wilcoxon_signed_rank([1, 2, 3, 4, 5, -6])["p"] == pytest.approx(28 / 64)
    assert wilcoxon_signed_rank([0.0, 0.0])["p"] is None, "nothing but zeros is no evidence either way"
    tied = wilcoxon_signed_rank([1.0, 1.0, -1.0, 2.0])
    assert tied["w_plus"] == pytest.approx(2.0 + 2.0 + 4.0), "ties share their mean rank"


def test_the_critical_value_is_right_beyond_thirty_degrees_of_freedom():
    """1.96 is the limit, not the value at 31 (2.040): an interval on 32
    instances was reported about 4 % narrower than it is."""
    from evolvekit.confirm import _t95

    exact = {1: 12.706, 30: 2.042, 31: 2.040, 40: 2.021, 60: 2.000, 120: 1.980, 1000: 1.962}
    for df, value in exact.items():
        assert _t95(df) == pytest.approx(value, abs=0.001), df
    values = [_t95(df) for df in range(1, 500)]
    assert all(a > b for a, b in zip(values, values[1:])), "it only ever falls towards 1.96"


def test_a_summary_says_in_words_what_the_interval_says():
    clear = paired_summary([1.0, 1.2, 0.8, 1.1, 0.9])
    assert clear["ci95"][0] > 0 and clear["verdict"].startswith("better than the baseline")
    assert (clear["wins"], clear["losses"]) == (5, 0)
    unclear = paired_summary([1.0, -1.2, 0.8, -0.7, 0.2])
    assert unclear["ci95"][0] < 0 < unclear["ci95"][1] and "includes zero" in unclear["verdict"]
    worse = paired_summary([-1.0, -1.2, -0.8, -1.1])
    assert worse["verdict"].startswith("WORSE")
    assert paired_summary([0.4])["ci95"] is None


def test_the_t_distribution_is_right():
    assert t_cdf(0.0, 5) == pytest.approx(0.5)
    assert t_cdf(4.303, 2) == pytest.approx(0.975, abs=0.001)
    assert t_cdf(-2.920, 2) == pytest.approx(0.05, abs=0.001)
    assert t_cdf(1.96, 2000) == pytest.approx(0.975, abs=0.001)
    assert t_cdf(1.0, 1) == pytest.approx(0.75, abs=0.001)


def test_the_statistics_live_in_one_module_and_confirm_re_exports_them():
    from evolvekit import confirm as confirm_module, stats

    for name in ("paired_summary", "t_cdf", "wilcoxon_signed_rank", "_t95", "_betainc"):
        assert getattr(confirm_module, name) is getattr(stats, name), name


def test_per_instance_means_average_the_seeds_within_each_instance():
    from evolvekit.stats import per_instance_means

    gains = {("a", 0): 1.0, ("a", 1): 3.0, ("b", 0): -2.0, ("b", 1): 0.0, ("c", 0): 5.0}
    assert per_instance_means(gains) == [2.0, -1.0, 5.0]
    assert per_instance_means({0: 1.5, 1: 2.5}) == [1.5, 2.5], "no instances: each seed is its own case"
    assert per_instance_means({}) == []


def test_a_summary_gives_the_chance_of_a_gain_and_the_worst_case():
    handoff = paired_summary([1.3, 0.6, 1.9])            # the design handoff's own study
    assert handoff["chance"] == pytest.approx(0.96, abs=0.01)
    assert handoff["worst"] == pytest.approx(handoff["ci95"][0])
    assert decide(handoff) == "use", "all three cheaper, worst case within 1 %"
    luck = paired_summary([-9.0, 11.5, -0.4])          # review round 1: -10.1 % .. 11.5 %
    assert 0.4 < luck["chance"] < 0.7 and decide(luck) == "keep"
    assert paired_summary([2.0, 2.0, 2.0])["chance"] == 1.0, "no spread, no division"
    assert decide(paired_summary([-2.0, -2.0, -2.0])) == "worse"
    assert decide(paired_summary([-1.5, -1.0, -1.2, -0.8])) == "worse"
    assert decide(paired_summary([5.0, 5.2, 4.8])) == "use"
    assert decide(paired_summary([0.5, 0.5, 0.5, 0.5, 0.5])) == "use"
    assert decide(paired_summary([-8.0, 1.0, 1.2])) == "keep", "one bad held-back case holds it back"
    assert decide(paired_summary([0.4])) == "single" and paired_summary([0.4])["chance"] is None
    assert decide(paired_summary([])) == "none"
    assert decide(paired_summary([3.0, 3.1, 2.9, -1.4])) == "keep", "chance is high but the worst case is beyond 1 %"


def test_a_gain_is_a_log_ratio_and_its_percentage_is_of_the_starting_point():
    from evolvekit.stats import log_gain, margin_log, pct_from_log

    assert log_gain(100.0, 90.0, "minimize") == pytest.approx(math.log(100 / 90))
    assert log_gain(100.0, 110.0, "maximize") == pytest.approx(math.log(1.1))
    assert log_gain(0.0, 90.0, "minimize") is None and log_gain(100.0, -1.0, "maximize") is None
    # Half as much and twice as much are one step either way: no -50 % against +100 %.
    assert log_gain(100.0, 50.0, "minimize") == pytest.approx(-log_gain(100.0, 200.0, "minimize"))
    assert pct_from_log(log_gain(100.0, 90.0, "minimize"), "minimize") == pytest.approx(10.0), "10 % lower"
    assert pct_from_log(log_gain(100.0, 110.0, "maximize"), "maximize") == pytest.approx(10.0), "10 % higher"
    for direction in ("minimize", "maximize"):
        assert pct_from_log(margin_log(1.0, direction), direction) == pytest.approx(-1.0)
    assert margin_log(1.0, "maximize") == pytest.approx(math.log(0.99)), "the design's x_m"
    with pytest.raises(ValueError):
        log_gain(1.0, 2.0, "sideways")


def test_the_comparison_averages_log_ratios_not_percentages_of_a_noisy_run():
    """S4: halving on one case and doubling on another is no change at all on the log
    scale. In percent of the starting point's run it was +50 % and -100 %: a mean of -25 %."""
    from evolvekit.confirm import BASELINE, Comparison, _analyse

    comparison = Comparison(label="t", objective="cost", direction="minimize", stage="full", seeds=[1],
                            instances=["a", "b"], baseline_id="g000-c0001", candidates=["c1"])
    for instance, mine in (("a", 50.0), ("b", 200.0)):
        comparison.runs += [
            {"configuration": BASELINE, "instance": instance, "seed": 1, "ok": True, "kpis": {"cost": 100.0}},
            {"configuration": "c1", "instance": instance, "seed": 1, "ok": True, "kpis": {"cost": mine}},
        ]
    _analyse(comparison)
    result = comparison.per_candidate["c1"]
    a, b = result["per_instance"]
    assert a["log_gain"] == pytest.approx(math.log(2)) and b["log_gain"] == pytest.approx(-math.log(2))
    assert (a["improvement_pct"], b["improvement_pct"]) == (pytest.approx(50.0), pytest.approx(-100.0))
    assert result["log_gains"] == [pytest.approx(math.log(2)), pytest.approx(-math.log(2))]
    assert result["summary"]["mean"] == pytest.approx(0.0, abs=1e-12)


def test_a_case_keeps_the_count_and_the_spread_of_the_gains_it_used():
    """What the posterior (A1.2) reads per case: the mean log gain, the variance of the
    gains it is the mean of, and how many there were -- failures imputed, pairs with a
    value of 0 or below dropped."""
    from evolvekit.confirm import BASELINE, Comparison, _analyse

    comparison = Comparison(label="t", objective="cost", direction="minimize", stage="full", seeds=[1, 2, 3],
                            instances=["a", "b"], baseline_id="g000-c0001", candidates=["c1"])
    runs = {  # (instance, seed): (baseline, candidate); None is a failed run
        ("a", 1): (100.0, 90.0), ("a", 2): (100.0, 80.0), ("a", 3): (100.0, None),
        ("b", 1): (100.0, 0.0), ("b", 2): (100.0, 95.0), ("b", 3): (None, None),
    }
    for (instance, seed), pair in runs.items():
        for who, value in zip((BASELINE, "c1"), pair):
            comparison.runs.append({"configuration": who, "instance": instance, "seed": seed, "ok": value is not None,
                                    "kpis": {} if value is None else {"cost": value}})
    _analyse(comparison)
    result = comparison.per_candidate["c1"]
    a, b = result["per_instance"]
    largest = math.log(100 / 80)
    used = [math.log(100 / 90), largest, -largest]
    assert result["failure_counted_as_log"] == pytest.approx(largest)
    assert (a["pairs"], a["log_gain"]) == (3, pytest.approx(sum(used) / 3))
    mean = sum(used) / 3
    assert a["seed_var"] == pytest.approx(sum((g - mean) ** 2 for g in used) / 2)
    assert (b["pairs"], b["nonpositive_pairs"], b["both_failed_pairs"]) == (1, 1, 1)
    assert b["log_gain"] == pytest.approx(math.log(100 / 95)) and b["seed_var"] is None
    assert result["pairs"] == 4 and result["nonpositive_pairs"] == 1


@pytest.mark.parametrize("direction, loss, win", [("minimize", 100.0, 50.0), ("maximize", 50.0, 100.0)])
def test_an_imputed_failure_is_said_as_the_loss_or_the_win_the_comparison_used(direction, loss, win):
    """The largest log gain measured is L = log 2. A pair only the candidate failed is
    counted as -L, one only the baseline failed as +L; in percent those are not mirror
    images (twice the cost is 100 % more, half is 50 % less), and the sentence says the
    number the comparison used."""
    from evolvekit.confirm import BASELINE, Comparison, _analyse, render_markdown

    better = 50.0 if direction == "minimize" else 200.0
    comparison = Comparison(label="t", objective="cost", direction=direction, stage="full", seeds=[1, 2, 3],
                            instances=["a", "b"], baseline_id="g000-c0001", candidates=["c1"])
    runs = {  # (instance, seed): (baseline, candidate); None is a failed run
        ("a", 1): (100.0, better), ("a", 2): (100.0, 100.0), ("a", 3): (100.0, None),
        ("b", 1): (100.0, 100.0), ("b", 2): (None, 100.0), ("b", 3): (100.0, 100.0),
    }
    for (instance, seed), pair in runs.items():
        for who, value in zip((BASELINE, "c1"), pair):
            comparison.runs.append({"configuration": who, "instance": instance, "seed": seed, "ok": value is not None,
                                    "kpis": {} if value is None else {"cost": value}})
    _analyse(comparison)
    result = comparison.per_candidate["c1"]
    assert result["failure_counted_as_log"] == pytest.approx(math.log(2))
    assert result["failure_counted_as_pct"] == {"loss": pytest.approx(loss), "win": pytest.approx(win)}
    a = result["per_instance"][0]
    assert a["log_gain"] == pytest.approx((math.log(2) + 0.0 - math.log(2)) / 3, abs=1e-12)
    verdict = result["summary"]["verdict"]
    assert f"1 pair(s) in which only the candidate failed counted as a loss of {loss:.3g} %" in verdict
    assert f"1 pair(s) in which only the baseline failed counted as a win of {win:.3g} %" in verdict
    assert f"(a loss of {loss:.3g} %, a win of {win:.3g} %)" in render_markdown(comparison)


def test_a_candidate_whose_every_run_equals_the_baseline_is_marked_identical():
    """Persona round 1, B2: every paired run of a branch gave exactly the starting point's result (its
    change never ran), and the comparison still gave it a chance and a range. `identical` says so; the
    decision is keep, and the check does not add runs to an answer more runs cannot open."""
    from evolvekit.confirm import BASELINE, Comparison, _analyse, render_markdown
    from evolvekit.harness.execute import _decision

    comparison = Comparison(label="final", objective="length", direction="minimize", stage="full", seeds=[1, 2, 3],
                            instances=["a", "b"], baseline_id="g000-c0001", candidates=["same", "other"])
    for instance, size in (("a", 10.0), ("b", 20.0)):
        for seed in (1, 2, 3):
            value = size + seed / 10
            for who, mine in ((BASELINE, value), ("same", value), ("other", value - (0.01 if (instance, seed) == ("b", 3) else 0))):
                comparison.runs.append({"configuration": who, "instance": instance, "seed": seed, "ok": True,
                                        "kpis": {"length": mine}})
    _analyse(comparison)
    same, other = comparison.per_candidate["same"], comparison.per_candidate["other"]
    assert same["identical"] is True and other["identical"] is False, "one run apart is a difference"
    assert _decision(same) == "keep"
    assert render_markdown(comparison).count("Every run gave exactly the baseline's result") == 1
    # A run that failed is not a run that gave today's result.
    comparison.runs = [r for r in comparison.runs if not (r["configuration"] == "same" and (r["instance"], r["seed"]) == ("a", 1))]
    _analyse(comparison)
    assert comparison.per_candidate["same"]["identical"] is False


# -- the comparison --------------------------------------------------------


def test_a_real_improvement_is_confirmed_on_fresh_seeds_instance_by_instance(tmp_path):
    config_path = _write_config(tmp_path)
    run_dir = _write_run(tmp_path, config_path, {"g003-c0012": {"params": {"x": -0.05}, "score": -95.0}})
    comparison = confirm(load_config(config_path), run_dir, seeds=[1001, 1003, 1004], label="test", log=lambda m: None)

    result = comparison.per_candidate["g003-c0012"]
    assert [r["instance"] for r in result["per_instance"]] == ["small", "medium", "large"]
    assert all(r["pairs"] == 3 for r in result["per_instance"])
    summary = result["summary"]
    assert summary["mean"] == pytest.approx(5.0, abs=0.6), "5 % better, whatever the instance's scale"
    assert summary["ci95"][0] > 0 and summary["wins"] == 3
    out = run_dir / "confirm" / "test"
    assert {p.name for p in out.iterdir()} >= {"results.json", "comparison.json", "comparison.md", "events.jsonl"}
    assert len(json.loads((out / "results.json").read_text(encoding="utf-8"))) == 2 * 3 * 3
    assert "**Mean improvement +" in (out / "comparison.md").read_text(encoding="utf-8")


def test_an_improvement_that_was_luck_is_reported_as_not_distinguishable(tmp_path):
    config_path = _write_config(tmp_path)
    # The search believed in this one (score -97), but `mode` does nothing at all.
    run_dir = _write_run(tmp_path, config_path, {"g002-c0007": {"params": {"mode": "deep"}, "score": -97.0}})
    comparison = confirm(load_config(config_path), run_dir, seeds=[1001, 1003], log=lambda m: None)
    summary = comparison.per_candidate["g002-c0007"]["summary"]
    assert summary["mean"] == pytest.approx(0.0, abs=1e-9)
    assert "WORSE" not in summary["verdict"] and not summary["verdict"].startswith("better")


def test_a_failed_run_costs_its_pair_not_the_comparison(tmp_path):
    config_path = _write_config(tmp_path)
    run_dir = _write_run(tmp_path, config_path, {"g003-c0012": {"params": {"x": -0.05}, "score": -95.0}})
    comparison = confirm(load_config(config_path), run_dir, seeds=[1001, 1002], log=lambda m: None)
    result = comparison.per_candidate["g003-c0012"]
    by_instance = {r["instance"]: r for r in result["per_instance"]}
    assert by_instance["medium"]["finished_pairs"] == 1, "seed 1002 crashed for the candidate on `medium`"
    assert by_instance["small"]["finished_pairs"] == 2 and by_instance["large"]["finished_pairs"] == 2
    assert result["finished_pairs"] == 5 and result["pairs_planned"] == 6 and result["failed_runs"] == 1


def test_a_pair_only_the_candidate_failed_counts_as_its_largest_loss(tmp_path):
    """A candidate that crashes or hangs where the baseline finished has lost
    that pair. Dropping the pair used to let a configuration that fails a lot
    be judged on its lucky runs alone. The loss is as large as the largest
    difference measured anywhere in the comparison, so a failure never weighs
    less than a real result."""
    config_path = _write_config(tmp_path)
    run_dir = _write_run(tmp_path, config_path, {"g003-c0012": {"params": {"x": -0.05}, "score": -95.0}})
    comparison = confirm(load_config(config_path), run_dir, seeds=[1001, 1002, 1003], log=lambda m: None)
    result = comparison.per_candidate["g003-c0012"]
    by_instance = {r["instance"]: r for r in result["per_instance"]}
    medium = by_instance["medium"]
    assert (medium["pairs"], medium["finished_pairs"], medium["candidate_failed_pairs"]) == (3, 2, 1)
    assert result["candidate_failed_pairs"] == 1 and result["failed_pairs"] == 1 and result["pairs"] == 9
    largest = result["failure_counted_as_log"]
    assert largest == pytest.approx(-math.log(0.95), abs=0.01), "the largest difference measured"
    sizes = result["failure_counted_as_pct"]
    # Minimised: the loss used is -largest, 100 * (exp(largest) - 1) % more (5.26 % at a ratio of
    # exactly 0.95; the largest pair here has some noise on top); a win is 100 * (1 - exp(-largest)) % less.
    assert sizes["loss"] == pytest.approx(100 * (math.exp(largest) - 1), rel=1e-12)
    assert sizes["win"] == pytest.approx(100 * (1 - math.exp(-largest)), rel=1e-12) and sizes["win"] < sizes["loss"]
    # About (5 + 5 - 5) / 3 on `medium`, against about 5 on `small`.
    assert medium["improvement_pct"] < by_instance["small"]["improvement_pct"] - 2.0
    assert result["summary"]["mean"] < result["finished_only"]["mean"]
    verdict = result["summary"]["verdict"]
    assert "1 pair(s) in which only the candidate failed counted as a loss of" in verdict
    out = run_dir / "confirm" / "confirm"
    written = json.loads((out / "comparison.json").read_text(encoding="utf-8"))["per_candidate"]["g003-c0012"]
    assert written["candidate_failed_pairs"] == 1 and written["finished_only"]["n"] == 3
    report = (out / "comparison.md").read_text(encoding="utf-8")
    assert "only the candidate failed counted as a loss" in report
    assert "| medium |" in report and "1 candidate failed" in report
    assert "Finished pairs only" in report


def test_a_pair_only_the_baseline_failed_counts_for_the_candidate_and_both_failing_counts_for_nobody(tmp_path):
    """The defaults hang too (PyVRP did, on the benchmark). The rule is the
    same both ways, and a pair in which both failed says nothing about either."""
    config_path = _write_config(tmp_path)
    solver = (tmp_path / "solver.py").read_text(encoding="utf-8")
    (tmp_path / "solver.py").write_text(
        solver.replace(
            "noise = random.Random",
            "if (a.instance, a.seed) == ('large', 1003) and a.x == 0.0:\n    sys.exit(3)\n"
            "if (a.instance, a.seed) == ('small', 1003):\n    sys.exit(4)\n"
            "noise = random.Random",
        ),
        encoding="utf-8",
    )
    run_dir = _write_run(tmp_path, config_path, {"g003-c0012": {"params": {"x": -0.05}, "score": -95.0}})
    comparison = confirm(load_config(config_path), run_dir, seeds=[1001, 1003], log=lambda m: None)
    result = comparison.per_candidate["g003-c0012"]
    by_instance = {r["instance"]: r for r in result["per_instance"]}
    assert by_instance["large"]["baseline_failed_pairs"] == 1 and by_instance["large"]["pairs"] == 2
    assert by_instance["large"]["improvement_pct"] > 0
    assert by_instance["small"]["both_failed_pairs"] == 1 and by_instance["small"]["pairs"] == 1
    verdict = result["summary"]["verdict"]
    assert "only the baseline failed counted as a win of" in verdict
    assert "1 pair(s) in which both failed are not in this interval" in verdict


def test_a_pair_with_a_value_of_zero_on_either_side_is_dropped_and_counted(tmp_path):
    """A log ratio of 0 is no number. Such a pair is dropped and the verdict says
    how many; a case whose every pair is dropped is not a case of the comparison."""
    config_path = _write_config(tmp_path)
    solver = (tmp_path / "solver.py").read_text(encoding="utf-8")
    (tmp_path / "solver.py").write_text(
        solver.replace("print(json.dumps({\"cost\": scale",
                       "print(json.dumps({\"cost\": 0.0 if (a.instance, a.x) == (\"small\", 0.0) else scale"),
        encoding="utf-8",
    )
    run_dir = _write_run(tmp_path, config_path, {"g003-c0012": {"params": {"x": -0.05}, "score": -95.0}})
    comparison = confirm(load_config(config_path), run_dir, seeds=[1001, 1003], log=lambda m: None)
    result = comparison.per_candidate["g003-c0012"]
    assert result["nonpositive_pairs"] == 2 and result["failed_pairs"] == 0 and result["pairs"] == 4
    assert result["per_instance"][0]["nonpositive_pairs"] == 2 and result["per_instance"][0]["log_gain"] is None
    assert result["summary"]["n"] == 2, "small has no comparable pair: it is not a case"
    assert "2 pair(s) had a value of 0 or below and are not in this comparison" in result["summary"]["verdict"]
    assert "zero_baseline_pairs" not in json.dumps(comparison.to_json())


def test_a_seed_given_twice_is_refused(tmp_path):
    config_path = _write_config(tmp_path)
    run_dir = _write_run(tmp_path, config_path, {"g003-c0012": {"params": {"x": -0.05}, "score": -95.0}})
    with pytest.raises(ValueError, match="--seeds: 1001 is given twice"):
        confirm(load_config(config_path), run_dir, seeds=[1001, 1003, 1001], log=lambda m: None)
    assert main(["confirm", "--config", str(config_path), "--run-dir", str(run_dir), "--seeds", "1001,1001"]) == 1


def test_the_baseline_is_the_seed_as_last_evaluated(tmp_path):
    """A run that aborted on its seed evaluates the seed again when it is run
    again: the first seed row is the failed attempt, the last one counts."""
    config_path = _write_config(tmp_path)
    run_dir = _write_run(tmp_path, config_path, {"g003-c0012": {"params": {"x": -0.05}, "score": -95.0}})
    rows = (run_dir / "runs.jsonl").read_text(encoding="utf-8").splitlines()
    seed = json.loads(rows[0])
    failed = {**seed, "id": "g000-c0000", "competes": False, "score": -1000.0, "last_failure": "stage full: exit 1"}
    retried = {**seed, "id": "g000-c0013"}
    lines = [json.dumps(failed), *rows[1:], json.dumps(retried)]
    (run_dir / "runs.jsonl").write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    comparison = confirm(load_config(config_path), run_dir, seeds=[1001], label="t", log=lambda m: None)
    assert comparison.baseline_id == "g000-c0013"


def test_top_n_and_instances_the_search_never_saw(tmp_path):
    config_path = _write_config(tmp_path)
    run_dir = _write_run(tmp_path, config_path, {
        "g001-c0002": {"params": {"x": -0.02}, "score": -98.0},
        "g002-c0005": {"params": {"x": -0.10}, "score": -90.0},
        "g003-c0009": {"params": {"x": 0.10}, "score": -110.0},
    })
    comparison = confirm(load_config(config_path), run_dir, seeds=[1001], candidates="top:2",
                         instances=["fresh"], label="fresh", log=lambda m: None)
    assert comparison.candidates == ["g002-c0005", "g001-c0002"] and comparison.instances == ["fresh"]
    assert comparison.per_candidate["g002-c0005"]["summary"]["mean"] == pytest.approx(10.0, abs=1.0)


def test_top_n_skips_a_candidate_the_hold_out_keeps_out():
    """A row written before the hold-out veto, `competes: true`, whose hold-out is
    below the seed's: the resumed search keeps it out, and so does `top:N`."""
    from evolvekit.confirm import _select

    def row(cid: str, operator: str, score: float, private: float) -> dict:
        return {"id": cid, "operator": operator, "score": score, "private_score": private,
                "rejected": False, "competes": True}

    rows = [row("g000-c0001", "human-seed", -100.0, -100.0), row("g001-c0002", "param_local", -90.0, -120.0),
            row("g001-c0003", "param_local", -95.0, -99.0)]
    assert [r["id"] for r in _select(rows, "top:1")] == ["g001-c0003"]


def test_an_interrupted_confirmation_does_not_pay_twice(tmp_path):
    config_path = _write_config(tmp_path)
    run_dir = _write_run(tmp_path, config_path, {"g003-c0012": {"params": {"x": -0.05}, "score": -95.0}})
    first = confirm(load_config(config_path), run_dir, seeds=[1001], log=lambda m: None)
    again = confirm(load_config(config_path), run_dir, seeds=[1001, 1003], log=lambda m: None)
    assert not any(r["cached"] for r in first.runs)
    assert sum(1 for r in again.runs if r["cached"]) == 6 and sum(1 for r in again.runs if not r["cached"]) == 6


def test_what_cannot_be_compared_is_refused_with_the_reason(tmp_path, minimal_raw):
    config_path = _write_config(tmp_path)
    config = load_config(config_path)
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="holds no seed candidate"):
        confirm(config, empty, seeds=[1], log=lambda m: None)
    run_dir = _write_run(tmp_path, config_path, {})
    with pytest.raises(ValueError, match="no fully evaluated candidate besides its seed"):
        confirm(config, run_dir, seeds=[1], log=lambda m: None)
    with pytest.raises(ValueError, match="holds no candidate 'g009-c0099'"):
        confirm(config, run_dir, seeds=[1], candidates="g009-c0099", log=lambda m: None)
    minimal_raw["evaluate"]["stages"].append({"id": "full", "kind": "command", "command": "e {candidate} {out}"})
    with pytest.raises(ConfigError, match="has to list `instances`"):
        confirm(build_config(minimal_raw, base_dir=tmp_path), run_dir, seeds=[1], log=lambda m: None)


# -- the winners of two runs -----------------------------------------------


def _two_runs(tmp_path: Path, config_path: Path) -> tuple[Path, Path]:
    """Two searches of the same problem: the first found x = -0.05, the second x = -0.10."""
    (tmp_path / "first").mkdir()
    (tmp_path / "second").mkdir()
    first = _write_run(tmp_path / "first", config_path, {"g003-c0012": {"params": {"x": -0.05}, "score": -95.0}})
    second = _write_run(tmp_path / "second", config_path, {"g003-c0012": {"params": {"x": -0.10}, "score": -90.0}})
    return first, second


def test_a_candidate_of_another_run_is_compared_in_the_same_interleaved_comparison(tmp_path):
    config_path = _write_config(tmp_path)
    first, second = _two_runs(tmp_path, config_path)
    comparison = confirm(load_config(config_path), second, seeds=[1001, 1003],
                         candidates=f"g003-c0012,g003-c0012@{first}", label="both", log=lambda m: None)
    # The same id in both runs: the other run's candidate carries its run directory's name.
    assert comparison.candidates == ["g003-c0012", "g003-c0012@run"]
    assert comparison.per_candidate["g003-c0012"]["summary"]["mean"] == pytest.approx(10.0, abs=0.8)
    assert comparison.per_candidate["g003-c0012@run"]["summary"]["mean"] == pytest.approx(5.0, abs=0.8)
    assert len(comparison.runs) == 3 * 3 * 2, "the baseline once, not once per run"


def test_against_names_what_the_candidates_are_compared_with(tmp_path):
    config_path = _write_config(tmp_path)
    first, second = _two_runs(tmp_path, config_path)
    comparison = confirm(load_config(config_path), second, seeds=[1001, 1003, 1004],
                         candidates="g003-c0012", against=f"g003-c0012@{first}", label="duel", log=lambda m: None)
    assert comparison.baseline_id == "g003-c0012@run"
    summary = comparison.per_candidate["g003-c0012"]["summary"]
    # 0.90 against 0.95: 5.26 % better than the other run's winner, not 10 % better than the seed.
    assert summary["mean"] == pytest.approx(100 * 0.05 / 0.95, abs=0.8) and summary["ci95"][0] > 0
    assert len(comparison.runs) == 2 * 3 * 3, "the seed is not run: it is not part of this comparison"
    report = (second / "confirm" / "duel" / "comparison.md").read_text(encoding="utf-8")
    assert "Baseline: `g003-c0012@run`" in report


def test_two_run_directories_with_the_same_name_are_told_apart(tmp_path):
    """Candidates of other runs were named `ID@<directory name>`: `a/run` and
    `b/run` both became `ID@run`, and two different configurations were
    refused as "compared with itself". The name grows by parent directories
    until it is unique."""
    config_path = _write_config(tmp_path)
    first, second = _two_runs(tmp_path, config_path)
    (tmp_path / "third").mkdir()
    third = _write_run(tmp_path / "third", config_path, {"g003-c0012": {"params": {"x": -0.02}, "score": -98.0}})
    comparison = confirm(load_config(config_path), second, seeds=[1001, 1003], candidates=f"g003-c0012@{first}",
                         against=f"g003-c0012@{third}", label="same-name", log=lambda m: None)
    assert comparison.baseline_id == "g003-c0012@third/run"
    assert comparison.candidates == ["g003-c0012@first/run"]
    both = confirm(load_config(config_path), second, seeds=[1001], candidates=f"g003-c0012@{first},g003-c0012@{third}",
                   label="both-named", log=lambda m: None)
    assert both.candidates == ["g003-c0012@first/run", "g003-c0012@third/run"]
    with pytest.raises(ValueError, match="compared twice under one name"):
        confirm(load_config(config_path), second, seeds=[1], candidates=f"g003-c0012@{first},g003-c0012@{first}",
                log=lambda m: None)


def test_a_candidate_that_is_not_there_is_refused_by_name(tmp_path):
    config_path = _write_config(tmp_path)
    config = load_config(config_path)
    first, second = _two_runs(tmp_path, config_path)
    with pytest.raises(ValueError, match="holds no run"):
        confirm(config, second, seeds=[1], candidates=f"g003-c0012@{tmp_path / 'nowhere'}", log=lambda m: None)
    with pytest.raises(ValueError, match="holds no candidate 'g009-c0099'"):
        confirm(config, second, seeds=[1], candidates=f"g009-c0099@{first}", log=lambda m: None)
    with pytest.raises(ValueError, match="--against: .* holds no candidate 'g009-c0099'"):
        confirm(config, second, seeds=[1], against="g009-c0099", log=lambda m: None)
    with pytest.raises(ValueError, match="compared with itself"):
        confirm(config, second, seeds=[1], candidates="g003-c0012", against="g003-c0012", log=lambda m: None)


# -- the command line ------------------------------------------------------


def test_the_exit_code_says_whether_the_improvement_is_real(tmp_path, capsys):
    config_path = _write_config(tmp_path)
    run_dir = _write_run(tmp_path, config_path, {"g003-c0012": {"params": {"x": -0.05}, "score": -95.0}})
    code = main(["confirm", "--config", str(config_path), "--run-dir", str(run_dir), "--seeds", "1001,1003,1004"])
    printed = capsys.readouterr().out
    assert code == 0 and "Better than the baseline" in printed and "| small |" in printed
    (tmp_path / "run2").mkdir()
    lucky = _write_run(tmp_path / "run2", config_path, {"g002-c0007": {"params": {"mode": "deep"}, "score": -97.0}})
    assert main(["confirm", "--config", str(config_path), "--run-dir", str(lucky), "--seeds", "1001,1003"]) == 1


def test_the_command_line_compares_the_winners_of_two_runs(tmp_path, capsys):
    config_path = _write_config(tmp_path)
    first, second = _two_runs(tmp_path, config_path)
    arguments = ["confirm", "--config", str(config_path), "--seeds", "1001,1003,1004", "--label", "duel"]
    # The second run's winner is the better one: exit 0 one way round, 1 the other.
    assert main([*arguments, "--run-dir", str(second), "--candidates", "g003-c0012",
                 "--against", f"g003-c0012@{first}"]) == 0
    assert "Baseline: `g003-c0012@run`" in capsys.readouterr().out
    assert main([*arguments, "--run-dir", str(first), "--candidates", "g003-c0012",
                 "--against", f"g003-c0012@{second}"]) == 1
    assert "Worse than the baseline" in capsys.readouterr().out


def test_export_hands_over_the_winner(tmp_path, capsys):
    config_path = _write_config(tmp_path)
    run_dir = _write_run(tmp_path, config_path, {
        "g001-c0002": {"params": {"x": -0.02}, "score": -98.0},
        "g002-c0005": {"params": {"x": -0.1, "mode": "deep"}, "score": -90.0},
    })
    assert main(["export", "--run-dir", str(run_dir)]) == 0
    assert json.loads(capsys.readouterr().out) == {"x": -0.1, "mode": "deep"}
    assert main(["export", "--run-dir", str(run_dir), "--format", "flags", "--config", str(config_path)]) == 0
    assert capsys.readouterr().out.strip() == "--x -0.1 --mode deep"
    assert main(["export", "--run-dir", str(run_dir), "--candidate", "g001-c0002", "--format", "yaml"]) == 0
    assert "x: -0.02" in capsys.readouterr().out
    out = tmp_path / "winner.json"
    assert main(["export", "--run-dir", str(run_dir), "--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["mode"] == "deep"
    assert main(["export", "--run-dir", str(run_dir), "--format", "code"]) == 0
    assert "def configure" in capsys.readouterr().out
    assert main(["export", "--run-dir", str(run_dir), "--candidate", "nope"]) == 1
    assert main(["export", "--run-dir", str(run_dir), "--format", "flags"]) == 1
