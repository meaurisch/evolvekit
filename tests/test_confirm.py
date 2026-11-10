"""`confirm`: the run's best against its baseline, paired, on seeds the search
never saw -- and `export`, the winner in a form another program can use."""

from __future__ import annotations

import json
import math
import random
import statistics
from pathlib import Path

import pytest

from evolvekit.cli import main
from evolvekit.config import ConfigError, build_config, load_config
from evolvekit.confirm import (
    BASELINE,
    USE_CHANCE,
    Comparison,
    _analyse,
    _level_summary,
    _chance2,
    _run_noise_of,
    confirm,
    decide,
    decision_of,
    paired_summary,
    t_cdf,
    verdict_summary,
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


def test_the_posterior_is_students_t_under_the_reference_prior():
    """Decision 11: p(mu, sigma^2) ~ 1/sigma^2 gives mu | d ~ t(n - 1, dbar, s / sqrt(n))."""
    from evolvekit.stats import posterior_mean_t

    d = [0.02, 0.01, 0.03, -0.01]
    dbar, s = statistics.fmean(d), statistics.stdev(d)
    post = posterior_mean_t(d)
    assert post.n == 4 and post.df == 3 and post.mu == pytest.approx(0.0125)
    assert post.scale == pytest.approx(s / 2)
    assert post.p_above(0.0) == pytest.approx(1 - t_cdf(-dbar / (s / 2), 3))
    assert post.p_above(0.0) == pytest.approx(paired_summary(d)["chance"]), "the one-sided t level, read as a posterior"
    low, high = post.interval95()
    assert (low, high) == pytest.approx(tuple(paired_summary(d)["ci95"]))
    assert high - low == pytest.approx(2 * 3.182 * post.scale, rel=1e-3)
    assert post.p_above(post.quantile_low(0.05)) == pytest.approx(0.95, abs=1e-6)
    with pytest.raises(ValueError):
        posterior_mean_t([])
    with pytest.raises(ValueError):
        posterior_mean_t([0.1])


def test_the_chance_shown_is_the_chance_the_rule_reads():
    d = [0.013, 0.006, 0.019]
    s = verdict_summary(d, direction="minimize")
    assert s["prior"] == "reference" and "prior_scale" not in s
    assert decide(s) == s["decision"] == "use"
    assert s["chance"] == pytest.approx(0.9611, abs=1e-4) and s["chance_margin"] == pytest.approx(0.9868, abs=1e-4)
    assert s["chance"] >= USE_CHANCE and s["worst"] >= 0, "use means the worst case shown is no loss"
    assert s["chance_margin_label"].endswith("it does not decide")
    assert s["ci95"][0] <= s["worst"] <= s["mean"], "worst is the 5 % quantile, inside the 95 % range"
    # Decision 15: the range a person is shown is the 90 % range, whose low end IS worst.
    assert s["range90"][0] == s["worst"] and s["ci95"][0] < s["range90"][0] < s["range90"][1] < s["ci95"][1]
    assert s["log"]["range90"][0] == s["log"]["worst"]
    assert _level_summary(d, "percent", "minimize")["range90"] == s["range90"]
    half = 2.920 / 3 ** 0.5  # t(2 df) at 95 %, scale sd / sqrt(n) = 1 / sqrt(3)
    assert _level_summary([1.0, 2.0, 3.0], "unit", "minimize")["range90"] == pytest.approx([2 - half, 2 + half], abs=1e-3)
    assert verdict_summary([0.01], direction="minimize")["range90"] is None
    assert s["mean"] == pytest.approx(100 * (1 - math.exp(-statistics.fmean(d)))), "the mean shown is the cases' mean"
    assert s["frequentist"]["label"].startswith("frequentist")
    assert "sd" not in s and s["log"]["sd"] == pytest.approx(statistics.stdev(d)), \
        "the spread is on the log scale and said so: no top-level sd beside percent figures"


def test_use_holds_exactly_when_the_chance_of_a_gain_is_19_in_20():
    """Decision 13a: use <=> chance >= 0.95. `worst` is the 5 % quantile, so a use's worst case
    shown is no loss; and the margin's chance is never below the chance of a gain (x_m < 0), so
    it could never have been the leg that said no."""
    rng = random.Random(7)
    for _ in range(300):
        d = [rng.gauss(rng.uniform(-0.02, 0.04), rng.uniform(0.001, 0.03)) for _ in range(rng.randint(2, 8))]
        s = verdict_summary(d, direction=rng.choice(["minimize", "maximize"]))
        assert (s["decision"] == "use") == (s["chance"] >= USE_CHANCE), s
        assert s["chance_margin"] >= s["chance"]
        if s["decision"] == "use":
            assert s["worst"] >= -1e-9


def test_no_spread_is_no_division():
    """Review Focus 1, as decisions 1 and 11 rule: with every effect equal the mean is
    known -- no division by zero, and the rule decides."""
    zero = verdict_summary([0.0, 0.0, 0.0], direction="minimize")
    assert zero["chance"] == 0.5 and zero["decision"] == "keep"
    same = verdict_summary([0.02, 0.02, 0.02], direction="minimize")
    assert same["chance"] == 1.0 and same["decision"] == "use"
    down = verdict_summary([-0.02, -0.02, -0.02], direction="maximize")
    assert down["chance"] == 0.0 and down["decision"] == "worse"
    near = verdict_summary([0.02, 0.021, 0.019], direction="minimize")
    assert 0.99 < near["chance"] < 1.0, "a little spread leaves a little doubt, finite and below 1"


def test_the_chance_is_not_capped():
    assert verdict_summary([0.05, 0.052, 0.048], direction="minimize")["chance"] > 0.99
    assert verdict_summary([0.26, 0.265], direction="maximize")["chance"] > 0.975, \
        "two cases of a 30 % gain with a small spread can say more than 39 in 40"


def test_the_rule_decides_on_the_posterior():
    assert decide(verdict_summary([-0.09, 0.115, -0.004], direction="minimize")) == "keep"
    assert decide(verdict_summary([-0.02, -0.02, -0.02], direction="minimize")) == "worse"
    assert decide(verdict_summary([-0.015, -0.010, -0.012, -0.008], direction="minimize")) == "worse"
    assert decide(verdict_summary([0.05, 0.052, 0.048], direction="minimize")) == "use"
    assert decide(verdict_summary([-0.08, 0.01, 0.012], direction="minimize")) == "keep"
    single = verdict_summary([0.004], direction="minimize")
    assert decide(single) == single["decision"] == "single" and single["chance"] is None
    assert single["verdict"] == "one instance: no chance can be given"
    nothing = verdict_summary([], direction="minimize")
    assert decide(nothing) == nothing["decision"] == "none" and nothing["verdict"] == "nothing to compare"


def test_the_sentence_says_the_decision_and_its_numbers():
    use = verdict_summary([0.05, 0.052, 0.048], direction="minimize")
    assert use["chance"] > 0.9995 and use["verdict"] == "probably better: a chance of a gain of 0.99 (reference prior)", \
        "floored: a chance below 1 never prints as 1.00"
    worse = verdict_summary([-0.015, -0.010, -0.012, -0.008], direction="minimize")
    assert worse["verdict"] == "probably WORSE: a chance of a gain of 0.01 (reference prior)",         "rounded up: a chance above 0 never prints as 0.00"
    keep = verdict_summary([-0.09, 0.115, -0.004], direction="minimize")
    assert keep["verdict"] == "not proven: a chance of a gain of 0.54 (reference prior)"
    assert _chance2(0.9496) == 0.94 and _chance2(0.996) == 0.99 and _chance2(0.95) == 0.95 and _chance2(0.0) == 0.0


def test_a_chance_never_prints_on_the_far_side_of_a_threshold():
    """Rounded to two places, then held on its own side of the rule's 0.95 and 0.05 (decision 14b),
    and of 1 and 0: a keep never prints 0.95 or 0.05, a chance below 1 never 1.00, above 0 never
    0.00. No epsilon: 0.94999999999999 is 0.94."""
    assert _chance2(0.9496) == 0.94 and _chance2(0.94999999999999) == 0.94 and _chance2(0.95) == 0.95
    assert _chance2(0.0504) == 0.06 and _chance2(0.05000000000001) == 0.06 and _chance2(0.05) == 0.05
    assert _chance2(0.996) == 0.99 and _chance2(0.0024) == 0.01 and _chance2(1.0) == 1.0 and _chance2(0.0) == 0.0
    assert _chance2(0.29) == 0.29 and _chance2(0.1011) == 0.10 and _chance2(0.5) == 0.5
    keep = verdict_summary([0.0, -0.002, -0.0005, -0.001, 0.0006], direction="minimize")
    assert keep["decision"] == "keep" and keep["chance"] < 0.5
    assert "a chance of a gain of 0.13" in keep["verdict"]


def test_worse_is_judged_as_strictly_as_use():
    """Decision 14b: worse at a chance of a gain of 0.05 or less, "5 %"; a comparison
    written before A1 keeps its own 0.10."""
    from evolvekit.confirm import WORSE_CHANCE

    assert WORSE_CHANCE == 0.05
    post = {"posterior": {"mu": 0.0, "scale": 1.0, "df": 3}}
    assert decide({"n": 4, "chance": 0.05, **post}) == "worse"
    assert decide({"n": 4, "chance": 0.07, **post}) == "keep"
    assert decide({"n": 4, "chance": 0.08, "worst": -3.0}) == "worse", "before A1: its 0.10"


def _crashing(seeds: int = 3, *, gain: float = 0.02, crash_seed: int | None = 2, levels: list | None = None):
    """Statistician round 2 (a): 4 cases, the candidate about `gain` better on every run it
    finishes, and crashing on seed `crash_seed` of every case, which the starting point finishes."""
    from evolvekit.confirm import BASELINE, Comparison, _analyse

    comparison = Comparison(label="x", objective="cost", direction="minimize", stage="full", seeds=list(range(seeds)),
                            instances=[f"i{i}" for i in range(4)], baseline_id="seed", candidates=["c"],
                            levels=levels or [])
    rng = random.Random(1)
    for i in range(4):
        for s in range(seeds):
            comparison.runs.append({"configuration": BASELINE, "instance": f"i{i}", "seed": s, "ok": True,
                                    "kpis": {"cost": 100.0, "late": 10.0}})
            ok = s != crash_seed
            g = gain + rng.uniform(-gain / 10, gain / 10)
            comparison.runs.append({"configuration": "c", "instance": f"i{i}", "seed": s, "ok": ok,
                                    "kpis": {"cost": 100 * math.exp(-g), "late": 10 * math.exp(-g)} if ok else {}})
    _analyse(comparison)
    return comparison


def test_a_candidate_that_fails_runs_the_starting_point_finished_is_never_use():
    """Decision 14a: crashing on one run in three of every case read use at 0.99996, because a crash
    costs at most its best finished gain. It is keep, and every sentence says why."""
    from evolvekit.confirm import render_markdown

    comparison = _crashing()
    result = comparison.per_candidate["c"]
    assert result["summary"]["chance"] > 0.99, "its chance alone would pass"
    assert result["summary"]["decision"] == "keep" and decision_of(result) == "keep"
    assert "it failed 4 runs the starting point finished" in result["summary"]["verdict"]
    assert result["summary"]["verdict"].startswith("not proven")
    text = render_markdown(comparison)
    assert "it failed 4 runs the starting point finished" in text and "Probably better" not in text
    stored = {**result, "summary": {k: v for k, v in result["summary"].items() if k != "decision"}}
    assert decision_of(stored) == "keep", "decided from what was stored, by the one rule"


def test_with_goals_in_order_a_failing_candidate_is_never_use_either():
    levels = [{"kpi": "cost", "direction": "minimize", "tolerance": None, "relative": False}]
    result = _crashing(levels=levels).per_candidate["c"]
    assert result["levels"]["decision"] == "keep" and result["levels"]["confirmed"] is False
    assert "it failed 4 runs the starting point finished" in result["levels"]["verdict"]
    assert decision_of(result) == "keep"


def test_a_failure_beside_tiny_gains_is_sized_at_least_one_percent():
    """Decision 14a: the S5 floor is `own largest < log(1.01)`, not `== 0`."""
    result = _crashing(gain=0.001).per_candidate["c"]
    assert result["failure_counted_as_log"] == pytest.approx(math.log(1.01))


def test_with_goals_in_order_the_page_bolds_the_levels_answer_not_the_objectives():
    """Statistician round 2 (b): the objective alone would say use, the levels say keep. The page's
    answer is the levels'; the objective's line is labelled as the engineers' and does not decide."""
    from evolvekit.confirm import BASELINE, OBJECTIVE_ALONE, Comparison, _analyse, render_markdown

    levels = [{"kpi": "late", "direction": "minimize", "tolerance": 0.01, "relative": True},
              {"kpi": "cost", "direction": "minimize", "tolerance": None, "relative": False}]
    comparison = Comparison(label="x", objective="score", direction="minimize", stage="full", seeds=[0, 1, 2],
                            instances=[f"i{i}" for i in range(4)], baseline_id="seed", candidates=["c"], levels=levels)
    late = [0.012, 0.006, 0.018, 0.009]  # about 1 % better: straddles the 1 % tolerance
    for i in range(4):
        for s in range(3):
            comparison.runs.append({"configuration": BASELINE, "instance": f"i{i}", "seed": s, "ok": True,
                                    "kpis": {"score": 100.0, "late": 50.0, "cost": 100.0}})
            comparison.runs.append({"configuration": "c", "instance": f"i{i}", "seed": s, "ok": True,
                                    "kpis": {"score": 100 * math.exp(-0.05 - 0.001 * i - 0.0005 * s),
                                             "late": 50 * math.exp(-late[i] - 0.001 * s), "cost": 90.0}})
    _analyse(comparison)
    result = comparison.per_candidate["c"]
    assert decision_of(result) == "keep" and result["summary"]["chance"] > 0.95
    assert result["summary"]["decision_label"] == OBJECTIVE_ALONE
    text = render_markdown(comparison)
    assert "Probably better" not in text and "**Not proven" in text
    assert "The objective alone, for engineers: it does not decide" in text


def test_a_comparison_written_before_the_posterior_decides_as_it_did():
    """Review Focus 3: a stored summary without the posterior keeps the rule it was decided by."""
    assert decide({"n": 3, "chance": 0.96, "worst": -0.5}) == "use"
    assert decide({"n": 3, "chance": 0.92, "worst": -0.5}) == "use", "the old rule's 90 %"
    assert decide({"n": 3, "chance": 0.96, "worst": -1.5}) == "keep"
    assert decide({"n": 3, "chance": 0.05, "worst": -4.0}) == "worse"
    assert decide(paired_summary([1.3, 0.6, 1.9])) == "use" and decide(paired_summary([3.0, 3.1, 2.9, -1.4])) == "keep"


def test_decision_of_reads_identical_then_levels_then_the_summary():
    use = verdict_summary([0.05, 0.052, 0.048], direction="minimize")
    assert decision_of({"summary": use}) == "use"
    assert decision_of({"summary": use, "identical": True}) == "keep"
    assert decision_of({"summary": use, "levels": {"confirmed": False}}) == "keep"
    assert decision_of({"summary": {"n": 0}, "levels": {"confirmed": True}}) == "use"


def test_the_run_to_run_noise_is_recorded_when_the_search_measured_it():
    assert _run_noise_of({"kpi_cv": {"cost": 0.012}}, "cost") == 0.012
    assert _run_noise_of({"kpi_cv": {"cost": 0.0}}, "cost") is None
    assert _run_noise_of({"kpi_cv": {"cost": None}}, "cost") is None
    assert _run_noise_of({}, "cost") is None


def _spread_of(n: int, *, mean: float, sd: float) -> list[float]:
    """`n` values with exactly this mean and sample sd."""
    z = [float(i) for i in range(n)]
    m, s = statistics.fmean(z), statistics.stdev(z)
    return [mean + sd * (v - m) / s for v in z]


def test_the_decision_is_monotone_in_the_mean_gain():
    order = {"worse": 0, "keep": 1, "use": 2}
    shape = [0.01, -0.02, 0.015, 0.0]
    seen = [order[decide(verdict_summary([x + k / 1000 for x in shape], direction="minimize"))]
            for k in range(-80, 81)]
    assert seen == sorted(seen) and seen[0] == 0 and seen[-1] == 2


def test_the_chance_grows_with_the_cases_for_a_fixed_mean_and_spread():
    chances = [verdict_summary(_spread_of(n, mean=0.01, sd=0.02), direction="minimize")["chance"]
               for n in range(2, 31)]
    assert all(a < b for a, b in zip(chances, chances[1:]))


def _a_study(rng: random.Random, *, gains: list[float], run_sd: float):
    """A synthetic final check of len(gains) held-back cases with these true log gains: each
    (case, run) has its own draw, fixed up front, so adding runs extends the same study.
    Returns `look(runs_per_case) -> result`, the real `_analyse` of the first runs."""
    draws = [[(rng.gauss(0.0, run_sd), rng.gauss(0.0, run_sd)) for _ in range(12)] for _ in gains]

    def look(runs: int) -> dict:
        comparison = Comparison(label="sim", objective="cost", direction="minimize", stage="full",
                                seeds=list(range(runs)), instances=[f"case{i}" for i in range(len(gains))],
                                baseline_id="seed", candidates=["c"], run_noise=run_sd or None)
        for i, gain in enumerate(gains):
            for s in range(runs):
                e_base, e_mine = draws[i][s]
                comparison.runs += [
                    {"configuration": BASELINE, "instance": f"case{i}", "seed": s, "ok": True,
                     "kpis": {"cost": 100.0 * math.exp(e_base)}},
                    {"configuration": "c", "instance": f"case{i}", "seed": s, "ok": True,
                     "kpis": {"cost": 100.0 * math.exp(-gain + e_mine)}},
                ]
        _analyse(comparison)
        return comparison.per_candidate["c"]

    return look


def _final_answer(look, *, runs: int = 3, most: int = 12) -> str:
    """The product's looks, as `execute._extend_check` and the page take them (decisions 12 and
    13b): look 1 after the planned runs; while it is open, every run up to `most` (the seeds,
    or the time) at once and look 2, whatever it says. The page offers one recheck with twice
    the runs only to an answer read once, and never to a check limited by its cases."""
    from evolvekit.confirm import runs_can_help

    result, looks = look(runs), 1
    if decision_of(result) == "keep" and runs < most and runs_can_help(result):
        runs, looks = most, 2
        result = look(runs)
    if (looks == 1 and decision_of(result) != "use" and runs < 12
            and runs_can_help(result)):  # a check limited by its cases is not checked again
        result = look(min(2 * runs, 12))
    return decision_of(result)


NULL_SETTINGS = {
    # name: (sd of the cases' true log gains around 0, run-to-run sd of a run's log objective,
    #        the most runs per case the extension reaches: the 12 seeds, or what the time pays for)
    "runs noisier than cases": (0.005, 0.02, 12),
    "cases noisier than runs": (0.02, 0.005, 12),
    "no replicate runs (every run of a case alike)": (0.02, 0.0, 12),
    "time-capped extension (to 4 runs, then no recheck)": (0.005, 0.02, 4),
}


def _null_use_rate(setting: str, studies: int, seed: int = 20261108) -> float:
    """The share of `studies` seeded null studies of `setting` that say use."""
    case_sd, run_sd, most = NULL_SETTINGS[setting]
    rng = random.Random(seed)
    uses = sum(_final_answer(_a_study(rng, gains=[rng.gauss(0.0, case_sd) for _ in range(4)], run_sd=run_sd),
                             most=most) == "use"
               for _ in range(studies))
    return uses / studies


@pytest.mark.parametrize("setting", list(NULL_SETTINGS))
def test_a_null_study_says_use_in_under_one_in_ten_under_the_products_looks(setting, capsys):
    """Decisions 11-13, the fast guard: with no true mean gain, 4 held-back cases x 3 runs, the
    extension's runs added at once when the answer is open (look 2), or else one recheck --
    at most two reads -- "use" in under 10 % of 1000 seeded studies, in each setting (the
    time-capped one extends to 4 runs and is then not rechecked). The slow test holds the margin."""
    rate = _null_use_rate(setting, 1000)
    with capsys.disabled():
        print(f"\nfalse use, {setting}, 4 cases x 3 runs, at most two reads (1000 studies): {rate:.3f}")
    assert rate < 0.10, f"{rate:.3f} of 1000 null studies said use"


@pytest.mark.slow
@pytest.mark.parametrize("setting", list(NULL_SETTINGS))
def test_a_null_study_says_use_in_under_one_in_ten_under_the_products_looks_two_standard_errors_clear(setting, capsys):
    """The same, on 6000 seeded studies per setting, so that 10 % is more than two standard
    errors above the measured rate (8.8 % in 1000 studies when runs are noisier than cases)."""
    studies = 6000
    rate = _null_use_rate(setting, studies)
    se = math.sqrt(rate * (1 - rate) / studies)
    with capsys.disabled():
        print(f"\nfalse use, {setting}, 4 cases x 3 runs, at most two reads ({studies} studies): "
              f"{rate:.4f} +- {se:.4f}")
    assert rate + 2 * se < 0.10, f"{rate:.4f} of {studies} null studies said use (2 SE = {2 * se:.4f})"


def _pure_noise_rate(answer: str, studies: int, seed: int = 11) -> tuple[float, float]:
    """The worst case: every case's true gain exactly 0, only the runs vary (0.2 %; the rule is
    scale-free, so their size does not matter). With no spread between cases more runs always
    help, so every open answer is extended and, read once, rechecked (`_final_answer`). Returns
    the share of `studies` seeded null studies that say `answer`, and its standard error."""
    rng = random.Random(seed)
    said = sum(_final_answer(_a_study(rng, gains=[0.0] * 4, run_sd=0.002)) == answer for _ in range(studies))
    rate = said / studies
    return rate, math.sqrt(rate * (1 - rate) / studies)


@pytest.mark.parametrize("answer", ["use", "worse"])
def test_a_null_study_of_pure_run_noise_says_use_or_worse_in_under_one_in_ten(answer, capsys):
    """The rule is P(gain > 0) >= 0.95 for use and <= 0.05 for worse (decision 14b: symmetric), the
    1 % margin never binding (decision 13a), so nothing but the chance stands between the null and
    either answer. Measured: use 8.6-9.0 % (8.9 % on 20 000 studies), worse 4.9-5.3 % (a worse read
    once is rechecked, a use is not). The fast guard: under 10 % of 4000 seeded studies (use 8.6 %,
    worse 5.3 % on this seed); the slow test holds the margin."""
    studies = 4000
    rate, se = _pure_noise_rate(answer, studies)
    with capsys.disabled():
        print(f"\nfalse {answer}, pure run noise, 4 cases x 3 runs, at most two reads ({studies} studies): "
              f"{rate:.4f} +- {se:.4f}")
    assert rate < 0.10, f"{rate:.4f} of {studies} null studies said {answer}"


@pytest.mark.slow
@pytest.mark.parametrize("answer", ["use", "worse"])
def test_a_null_study_of_pure_run_noise_says_use_or_worse_in_under_one_in_ten_two_standard_errors_clear(
        answer, capsys):
    """The same on 8000 seeded studies, so that 10 % is more than two standard errors above the rate."""
    studies = 8000
    rate, se = _pure_noise_rate(answer, studies, seed=12)
    with capsys.disabled():
        print(f"\nfalse {answer}, pure run noise, 4 cases x 3 runs, at most two reads ({studies} studies): "
              f"{rate:.4f} +- {se:.4f}")
    assert rate + 2 * se < 0.10, f"{rate:.4f} of {studies} null studies said {answer} (2 SE = {2 * se:.4f})"


def test_a_three_percent_gain_on_four_cases_reports_its_power(capsys):
    rng = random.Random(3)
    uses = sum(_final_answer(_a_study(rng, gains=[rng.gauss(0.03, 0.005) for _ in range(4)], run_sd=0.02)) == "use"
               for _ in range(200))
    with capsys.disabled():
        print(f"\npower of a 3 % gain on 4 held-back cases x 3 runs, at most two reads: {uses / 200:.2f}")
    assert 0 <= uses <= 200


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


def test_a_failure_of_a_candidate_that_otherwise_equals_the_starting_point_is_never_a_loss_of_0_percent():
    """S5: a candidate whose finished pairs all equal the starting point has its own largest gain
    0; its crash is sized by the largest gain of any candidate's finished pairs in the comparison,
    and by log 1.01 (a 1 % loss) when that is 0 too. A candidate with a gain of its own keeps its own."""
    from evolvekit.confirm import BASELINE, Comparison, _analyse

    def compare(others: dict[str, float]) -> Comparison:
        comparison = Comparison(label="t", objective="cost", direction="minimize", stage="full", seeds=[1, 2],
                                instances=["a", "b"], baseline_id="g000-c0001", candidates=["same", *others])
        for instance in ("a", "b"):
            for seed in (1, 2):
                comparison.runs.append({"configuration": BASELINE, "instance": instance, "seed": seed, "ok": True,
                                        "kpis": {"cost": 100.0}})
                crashed = (instance, seed) == ("a", 2)
                comparison.runs.append({"configuration": "same", "instance": instance, "seed": seed,
                                        "ok": not crashed, "kpis": {} if crashed else {"cost": 100.0}})
                for cid, value in others.items():
                    comparison.runs.append({"configuration": cid, "instance": instance, "seed": seed, "ok": True,
                                            "kpis": {"cost": value if seed == 1 else 100.0}})
        _analyse(comparison)
        return comparison

    comparison = compare({"rival": 80.0})
    same = comparison.per_candidate["same"]
    assert same["failure_counted_as_log"] == pytest.approx(math.log(100 / 80)), "the comparison's largest"
    assert same["per_instance"][0]["log_gain"] == pytest.approx(-math.log(100 / 80) / 2)
    assert "counted as a loss of 0 %" not in same["summary"]["verdict"]
    assert comparison.per_candidate["rival"]["failure_counted_as_log"] == pytest.approx(math.log(100 / 80))
    alone = compare({})
    assert alone.per_candidate["same"]["failure_counted_as_log"] == pytest.approx(math.log(1.01))
    assert "counted as a loss of 1 %" in alone.per_candidate["same"]["summary"]["verdict"]


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
    assert summary["chance"] == 0.5 and summary["decision"] == "keep"


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
    assert code == 0 and "Probably better" in printed and "| small |" in printed
    (tmp_path / "run2").mkdir()
    lucky = _write_run(tmp_path / "run2", config_path, {"g002-c0007": {"params": {"mode": "deep"}, "score": -97.0}})
    assert main(["confirm", "--config", str(config_path), "--run-dir", str(lucky), "--seeds", "1001,1003"]) == 1


def test_comparison_json_records_the_prior_and_how_often_the_answer_was_read(tmp_path):
    config_path = _write_config(tmp_path)
    run_dir = _write_run(tmp_path, config_path, {"g003-c0012": {"params": {"x": -0.05}, "score": -95.0}})
    first = confirm(load_config(config_path), run_dir, seeds=[1001], label="final", log=lambda m: None)
    again = confirm(load_config(config_path), run_dir, seeds=[1001, 1003], label="final", log=lambda m: None)
    written = json.loads((run_dir / "confirm" / "final" / "comparison.json").read_text(encoding="utf-8"))
    assert first.looks == 1 and again.looks == 2 and written["looks"] == 2
    assert written["prior"] == "reference"
    assert written["run_noise"] is None, "the hand-written seed row has no kpi_cv"
    summary = written["per_candidate"]["g003-c0012"]["summary"]
    assert {"chance", "chance_margin", "worst", "ci95", "frequentist", "posterior", "decision"} <= set(summary)
    markdown = (run_dir / "confirm" / "final" / "comparison.md").read_text(encoding="utf-8")
    assert "The answer was read 2 time(s)." in markdown and "Frequentist, for engineers:" in markdown


def test_a_new_comparison_under_the_same_label_starts_its_own_count_of_reads(tmp_path):
    """I2: after Continue the final check under `final` compares another candidate: its answer
    was never read, so it starts at one read, not at the old comparison's count plus one."""
    config_path = _write_config(tmp_path)
    run_dir = _write_run(tmp_path, config_path, {"g003-c0012": {"params": {"x": -0.05}, "score": -95.0},
                                                 "g004-c0020": {"params": {"x": -0.04}, "score": -96.0}})
    config = load_config(config_path)
    first = confirm(config, run_dir, seeds=[1001], candidates="g003-c0012", label="final", log=lambda m: None)
    other = confirm(config, run_dir, seeds=[1001], candidates="g004-c0020", label="final", log=lambda m: None)
    assert first.looks == 1 and other.looks == 1
    written = json.loads((run_dir / "confirm" / "final" / "comparison.json").read_text(encoding="utf-8"))
    assert written["looks"] == 1


def test_a_read_that_ran_nothing_new_is_not_another_read(tmp_path):
    """I2 (M): the same comparison read again from its cached runs alone measured nothing new."""
    config_path = _write_config(tmp_path)
    run_dir = _write_run(tmp_path, config_path, {"g003-c0012": {"params": {"x": -0.05}, "score": -95.0}})
    config = load_config(config_path)
    first = confirm(config, run_dir, seeds=[1001, 1003], label="final", log=lambda m: None)
    same = confirm(config, run_dir, seeds=[1001, 1003], label="final", log=lambda m: None)
    assert all(run["cached"] for run in same.runs)
    assert first.looks == 1 and same.looks == 1
    more = confirm(config, run_dir, seeds=[1001, 1003, 1004], label="final", log=lambda m: None)
    assert more.looks == 2


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
    assert "Probably WORSE" in capsys.readouterr().out


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


def test_the_share_of_the_runs_is_within_over_within_plus_between():
    from evolvekit.confirm import within_share

    rows = [{"log_gain": 0.10, "seed_var": 0.0004, "pairs": 4}, {"log_gain": -0.08, "seed_var": 0.0004, "pairs": 4},
            {"log_gain": 0.01, "seed_var": 0.0004, "pairs": 4}]
    within = 0.0001
    between = statistics.variance([0.10, -0.08, 0.01]) - within
    assert within_share(rows) == pytest.approx(within / (within + between))
    assert within_share(rows[:1]) is None, "one case has no spread between cases"
    assert within_share([{**r, "seed_var": None} for r in rows]) is None, "one run per case: unknown"
    assert within_share([{"log_gain": 0.0, "seed_var": 0.0, "pairs": 3}] * 3) == 0.0
    # Pooled over the cases that have a variance; a one-run case has none but its mean is as noisy.
    mixed = [{"log_gain": 0.10, "seed_var": 0.0006, "pairs": 3}, {"log_gain": -0.05, "seed_var": 0.0002, "pairs": 3},
             {"log_gain": 0.02, "seed_var": None, "pairs": 1}]
    within_mixed = (0.0006 + 0.0002) / 2 * (1 / 3 + 1 / 3 + 1) / 3
    between_mixed = max(0.0, statistics.variance([0.10, -0.05, 0.02]) - within_mixed)
    assert within_share(mixed) == pytest.approx(within_mixed / (within_mixed + between_mixed))
    # Decision 7: the gains actually used (`pairs`), not the case's total, divide the variance.
    fewer = [{**r, "pairs": 2} for r in rows]
    assert within_share(fewer) > within_share(rows)


def test_runs_dominated_by_their_own_noise_are_called_case_limited_in_under_one_in_ten_checks():
    """With no difference between cases the share test stops wrongly when var(d) > 4 x within,
    an F(3, 8) tail of about 5 %."""
    from evolvekit.confirm import runs_can_help

    rng = random.Random(25)
    limited = sum(not runs_can_help(_a_study(rng, gains=[0.0] * 4, run_sd=0.02)(3)) for _ in range(400))
    assert 5 <= limited < 40, f"{limited} of 400 (about 5 % expected: 21)"


def test_holm_steps_down():
    from evolvekit.stats import holm

    assert holm({"a": 0.01, "b": 0.03, "c": 0.04}, 0.05) == {"a": True, "b": False, "c": False}
    assert holm({"a": 0.01, "b": 0.02, "c": 0.04}, 0.05) == {"a": True, "b": True, "c": True}
    assert holm({"a": 0.02, "b": 0.03, "c": 0.04}, 0.05) == {"a": False, "b": False, "c": False}
    assert holm({"a": 0.04}, 0.05) == {"a": True} and holm({}, 0.05) == {}


def _posterior_summary(chance: float) -> dict:
    return {"n": 4, "chance": chance, "chance_margin": chance, "worst": 1.0, "posterior": {}}


def test_top_n_pays_for_choosing_among_its_candidates():
    from evolvekit.confirm import apply_holm

    results = {"g1": {"summary": _posterior_summary(0.999)}, "g2": {"summary": _posterior_summary(0.96)},
               "g3": {"summary": _posterior_summary(0.5)}}
    apply_holm(results, what="candidates")
    assert decide(results["g2"]["summary"]) == "use", "alone, 0.96 passes the rule"
    assert decision_of(results["g1"]) == "use" and decision_of(results["g2"]) == "keep"
    assert results["g2"]["holm"] == {"k": 3, "what": "candidates", "own": "use", "passed": False}
    alone = {"g2": {"summary": _posterior_summary(0.96)}}
    apply_holm(alone, what="candidates")
    assert "holm" not in alone["g2"] and decision_of(alone["g2"]) == "use", "one is no family"
    old = {"a": {"summary": {"n": 4, "chance": 0.95, "worst": 0.1}}, "b": {"summary": _posterior_summary(0.96)},
           "c": {"summary": _posterior_summary(0.5), "identical": True}}
    apply_holm(old, what="branches")
    assert not any("holm" in r for r in old.values()), "a comparison before A1, and an identical run, are no family"


def test_four_null_branches_say_use_in_at_most_eight_percent_of_bakeoffs_with_holm():
    """Family-wise false "use" with k = 4 equal branches: Holm bounds it by alpha = 5 %
    (asserted at 8 % over 400 seeded bake-offs, i.e. plus sampling margin)."""
    from evolvekit.confirm import apply_holm

    rng = random.Random(32)
    runs, any_use, any_use_alone = 400, 0, 0
    for _ in range(runs):
        results = {f"b{i}": _a_study(rng, gains=[rng.gauss(0.0, 0.02) for _ in range(4)], run_sd=0.005)(3)
                   for i in range(4)}
        any_use_alone += any(decision_of(r) == "use" for r in results.values())
        apply_holm(results, what="branches")
        any_use += any(decision_of(r) == "use" for r in results.values())
    print(f"family-wise false use, 4 null branches: {any_use / runs:.3f} with Holm, {any_use_alone / runs:.3f} without")
    assert any_use <= 0.08 * runs, f"{any_use} of {runs} bake-offs of four equal branches named one better"
    assert any_use <= any_use_alone


def test_top_two_over_several_cases_is_a_family_and_says_so(tmp_path):
    config_path = _write_config(tmp_path)
    run_dir = _write_run(tmp_path, config_path, {
        "g001-c0002": {"params": {"x": -0.02}, "score": -98.0},
        "g002-c0005": {"params": {"x": -0.10}, "score": -90.0},
    })
    comparison = confirm(load_config(config_path), run_dir, seeds=[1001], candidates="top:2", label="fam",
                         log=lambda m: None)
    written = json.loads((run_dir / "confirm" / "fam" / "comparison.json").read_text(encoding="utf-8"))
    assert len(comparison.instances) >= 2
    for cid in comparison.candidates:
        assert written["per_candidate"][cid]["holm"]["k"] == 2 and written["per_candidate"][cid]["holm"]["what"] == "candidates"
    assert "Chosen among 2 candidates (Holm)" in (run_dir / "confirm" / "fam" / "comparison.md").read_text(encoding="utf-8")
    alone = confirm(load_config(config_path), run_dir, seeds=[1001], candidates="top:1", label="one", log=lambda m: None)
    assert "holm" not in alone.per_candidate[alone.candidates[0]]


def test_the_markdown_of_a_candidate_holm_held_back_does_not_say_better():
    look = _a_study(random.Random(5), gains=[0.03, 0.031, 0.029, 0.03], run_sd=0.002)(3)
    assert decision_of(look) == "use"
    look["holm"] = {"k": 3, "what": "candidates", "own": "use", "passed": False}
    comparison = Comparison(label="sim", objective="cost", direction="minimize", stage="full", seeds=[0, 1, 2],
                            instances=["a", "b", "c", "d"], baseline_id="seed", candidates=["c"])
    comparison.per_candidate["c"] = look
    from evolvekit.confirm import render_markdown

    text = render_markdown(comparison)
    assert "Chosen among 3 candidates (Holm): does not pass" in text
    assert "Not proven among 3 candidates (Holm)" in text and "Probably better" not in text


def _equal_but_the_starting_point_crashes(crashed_cases: set[int], cases: int, *, gain: float = 0.0):
    """Statistician round 3 (R1): the candidate equals the starting point on every finished pair
    (or is about `gain` better), and the starting point crashes on seed 1 of `crashed_cases`."""
    from evolvekit.confirm import BASELINE, Comparison, _analyse

    comparison = Comparison(label="x", objective="cost", direction="minimize", stage="full", seeds=[0, 1, 2],
                            instances=[f"c{i}" for i in range(cases)], baseline_id="seed", candidates=["c"])
    rng = random.Random(3)
    for i in range(cases):
        for s in range(3):
            value, ok = 100.0 + i, not (i in crashed_cases and s == 1)
            comparison.runs.append({"configuration": BASELINE, "instance": f"c{i}", "seed": s, "ok": ok,
                                    "kpis": {"cost": value} if ok else {}})
            g = gain + rng.uniform(-gain / 10, gain / 10)
            comparison.runs.append({"configuration": "c", "instance": f"c{i}", "seed": s, "ok": True,
                                    "kpis": {"cost": value * math.exp(-g)}})
    _analyse(comparison)
    return comparison.per_candidate["c"]


@pytest.mark.parametrize("crashed, cases", [({0, 1}, 2), ({0, 1, 2}, 4), ({0, 1, 2, 3}, 4)])
def test_a_pair_only_the_starting_point_failed_is_no_win_for_a_change_that_does_nothing(crashed, cases):
    """R1: the 1 % floor sizes the candidate's own failures only. A crash of the starting point
    beside equal finished pairs counts as the candidate's own largest gain, 0: no use."""
    result = _equal_but_the_starting_point_crashes(crashed, cases)
    assert decision_of(result) == "keep" and result["summary"]["decision"] == "keep"
    assert result["failure_counted_as_pct"]["win"] == pytest.approx(0.0)
    assert "counted as a win of 0 %" in result["summary"]["verdict"]


def test_a_real_gain_beside_starting_point_crashes_still_reads_use():
    result = _equal_but_the_starting_point_crashes({0, 1, 2, 3}, 4, gain=0.03)
    assert decision_of(result) == "use"
    assert result["failure_counted_as_pct"]["win"] > 3.0, "a win as large as its own largest gain"


def test_a_candidate_failure_beside_a_starting_point_failure_is_floored_alike_on_either_side():
    """Round 4: once a candidate-only failure is floored at 1 %, a starting-point-only failure is
    a win of the same size, so failures that strike both programs alike cancel out."""
    from evolvekit.confirm import BASELINE, Comparison, _analyse

    comparison = Comparison(label="x", objective="cost", direction="minimize", stage="full", seeds=[0, 1],
                            instances=["a", "b"], baseline_id="seed", candidates=["c"])
    for instance in ("a", "b"):
        for s in (0, 1):
            base_ok, mine_ok = (instance, s) != ("a", 1), (instance, s) != ("b", 1)
            comparison.runs.append({"configuration": BASELINE, "instance": instance, "seed": s, "ok": base_ok,
                                    "kpis": {"cost": 100.0} if base_ok else {}})
            comparison.runs.append({"configuration": "c", "instance": instance, "seed": s, "ok": mine_ok,
                                    "kpis": {"cost": 100.0} if mine_ok else {}})
    _analyse(comparison)
    result = comparison.per_candidate["c"]
    assert result["failure_counted_as_log"] == pytest.approx(math.log(1.01))
    assert result["failure_counted_as_pct"]["loss"] == pytest.approx(1.0)
    assert result["failure_counted_as_pct"]["win"] == pytest.approx(100 * (1 - 1 / 1.01))
    a, b = result["per_instance"]
    assert a["log_gain"] == pytest.approx(math.log(1.01) / 2) and b["log_gain"] == pytest.approx(-math.log(1.01) / 2)
    assert decision_of(result) == "keep"


def _levels_use():
    from evolvekit.confirm import BASELINE, Comparison, _analyse

    levels = [{"kpi": "cost", "direction": "minimize", "tolerance": 0.01, "relative": True},
              {"kpi": "late", "direction": "minimize", "tolerance": None, "relative": False}]
    comparison = Comparison(label="x", objective="cost", direction="minimize", stage="full", seeds=[0, 1, 2],
                            instances=[f"i{i}" for i in range(4)], baseline_id="seed", candidates=["c"], levels=levels)
    for i in range(4):
        for s in range(3):
            comparison.runs.append({"configuration": BASELINE, "instance": f"i{i}", "seed": s, "ok": True,
                                    "kpis": {"cost": 100.0, "late": 10.0}})
            comparison.runs.append({"configuration": "c", "instance": f"i{i}", "seed": s, "ok": True,
                                    "kpis": {"cost": 100 * math.exp(-0.05 - 0.001 * i - 0.0005 * s), "late": 10.0}})
    _analyse(comparison)
    assert comparison.per_candidate["c"]["levels"]["decision"] == "use"
    return comparison


def test_a_holm_held_levels_result_says_not_proven_beside_its_clear_level():
    """R2: under levels, a Holm-held result's levels verdict says why it is not use, in both places."""
    from evolvekit.confirm import render_markdown

    comparison = _levels_use()
    result = comparison.per_candidate["c"]
    plain = result["levels"]["verdict"]
    result["holm"] = {"k": 3, "what": "candidates", "own": "use", "passed": False}
    text = render_markdown(comparison)
    said = plain + "; not proven among 3 candidates (Holm)"
    assert f"**Not proven: {said}.**" in text
    assert f"**{said[:1].upper() + said[1:]}.**" in text
    assert "Probably better" not in text
    del result["holm"]
    result["identical"] = True
    text = render_markdown(comparison)
    assert f"**Not proven: {plain}; every run gave the starting point's result.**" in text


def test_comparison_json_names_the_answer_after_holm():
    """R3: a Holm-held candidate's result carries `decision` after Holm; its summary's decision is
    labelled as the answer before it."""
    from evolvekit.confirm import BEFORE_HOLM, apply_holm

    results = {"g1": {"summary": _posterior_summary(0.999)}, "g2": {"summary": _posterior_summary(0.96)},
               "g3": {"summary": _posterior_summary(0.5)}}
    for r in results.values():
        r["summary"]["decision"] = decide(r["summary"])
    apply_holm(results, what="candidates")
    assert results["g2"]["decision"] == "keep" and results["g2"]["summary"]["decision"] == "use"
    assert results["g2"]["summary"]["decision_label"] == BEFORE_HOLM
    assert results["g1"]["decision"] == "use" and results["g3"]["decision"] == "keep"
    one, two = _levels_use().per_candidate["c"], _levels_use().per_candidate["c"]
    apply_holm({"a": one, "b": two}, what="candidates")
    assert one["decision"] == decision_of(one)
    assert one["summary"]["decision_label"] != BEFORE_HOLM, "under levels the objective's label stays"


def test_a_relative_tolerance_on_a_level_whose_starting_point_is_0_is_0():
    """R4: a fraction of 0 is 0, never the tolerance in the measure's own units: only equal is equal."""
    from evolvekit.confirm import BASELINE, Comparison, _analyse

    levels = [{"kpi": "late", "direction": "minimize", "tolerance": 0.05, "relative": True},
              {"kpi": "cost", "direction": "minimize", "tolerance": None, "relative": False}]

    def compare(late: list[float]):
        comparison = Comparison(label="x", objective="cost", direction="minimize", stage="full", seeds=[0, 1],
                                instances=[f"i{i}" for i in range(4)], baseline_id="seed", candidates=["c"],
                                levels=levels)
        for i in range(4):
            for s in range(2):
                comparison.runs.append({"configuration": BASELINE, "instance": f"i{i}", "seed": s, "ok": True,
                                        "kpis": {"cost": 100.0, "late": 0.0}})
                comparison.runs.append({"configuration": "c", "instance": f"i{i}", "seed": s, "ok": True,
                                        "kpis": {"cost": 90.0, "late": late[i]}})
        _analyse(comparison)
        return comparison.per_candidate["c"]["levels"]["levels"][0]

    row = compare([0.01, 0.02, 0.03, 0.04])
    assert row["tolerance_abs"] == 0.0 and row["state"] != "equal"
    assert compare([0.0] * 4)["state"] == "equal"


def test_a_comparison_written_before_a1_keeps_its_use_beside_failures():
    """R5: the failure rule is A1's; a stored legacy use keeps its answer."""
    legacy = {"summary": {"n": 3, "chance": 0.96, "worst": -0.5},
              "candidate_failed_pairs": 2, "baseline_failed_pairs": 0}
    assert decision_of(legacy) == "use"
    a1 = {"summary": _posterior_summary(0.99), "candidate_failed_pairs": 2, "baseline_failed_pairs": 0}
    assert decision_of(a1) == "keep"


def _flaky_on_both_sides(rng: random.Random, *, failure_rate: float, cases: int, runs: int, run_sd: float) -> str:
    """Statistician round 4: the same program on both sides, each run failing independently at
    `failure_rate` (timeouts, a flaky machine) and finished runs varying by `run_sd` on the log
    scale (0: deterministic). Returns the answer of one comparison."""
    comparison = Comparison(label="x", objective="cost", direction="minimize", stage="full", seeds=list(range(runs)),
                            instances=[f"c{i}" for i in range(cases)], baseline_id="seed", candidates=["c"])
    for i in range(cases):
        for s in range(runs):
            for who in (BASELINE, "c"):
                ok = rng.random() >= failure_rate
                value = (100.0 + i) * math.exp(rng.gauss(0.0, run_sd)) if run_sd else 100.0 + i
                comparison.runs.append({"configuration": who, "instance": f"c{i}", "seed": s, "ok": ok,
                                        "kpis": {"cost": value} if ok else {}})
    _analyse(comparison)
    return decision_of(comparison.per_candidate["c"])


FLAKY_SETTINGS = {  # name: (failure rate, cases, runs, run sd on the log scale)
    "deterministic, 10 cases x 3 runs, 10 % of runs fail": (0.10, 10, 3, 0.0),
    "deterministic, 10 cases x 1 run, 20 % of runs fail": (0.20, 10, 1, 0.0),
    "run noise 0.2 %, 10 cases x 3 runs, 10 % of runs fail": (0.10, 10, 3, 0.002),
}


def _flaky_rates(setting: str, studies: int, seed: int = 4) -> dict[str, tuple[float, float]]:
    """The share of `studies` seeded comparisons of `setting` that say use and worse, each with its SE."""
    failure_rate, cases, runs, run_sd = FLAKY_SETTINGS[setting]
    rng = random.Random(seed)
    answers = [_flaky_on_both_sides(rng, failure_rate=failure_rate, cases=cases, runs=runs, run_sd=run_sd)
               for _ in range(studies)]
    rates = {}
    for answer in ("use", "worse"):
        rate = answers.count(answer) / studies
        rates[answer] = (rate, math.sqrt(rate * (1 - rate) / studies))
    return rates


def test_a_do_nothing_change_beside_failures_on_both_sides_reads_keep():
    """Round 4: 10 cases x 1 run, every finished pair equal; the candidate fails cases 0-2 and the
    starting point cases 3-5. Its three losses and three wins are sized alike, so the chance is
    0.5, not "probably worse" (it was 0.04)."""
    comparison = Comparison(label="x", objective="cost", direction="minimize", stage="full", seeds=[0],
                            instances=[f"i{k}" for k in range(10)], baseline_id="seed", candidates=["c"])
    for k in range(10):
        for who, ok in ((BASELINE, k not in (3, 4, 5)), ("c", k not in (0, 1, 2))):
            comparison.runs.append({"configuration": who, "instance": f"i{k}", "seed": 0, "ok": ok,
                                    "kpis": {"cost": 100.0} if ok else {}})
    _analyse(comparison)
    result = comparison.per_candidate["c"]
    assert decision_of(result) == "keep"
    assert result["summary"]["chance"] == pytest.approx(0.5)
    assert result["failure_counted_as_pct"]["win"] == pytest.approx(100 * (1 - 1 / 1.01))


@pytest.mark.parametrize("setting", list(FLAKY_SETTINGS))
def test_failures_that_strike_both_sides_alike_say_use_or_worse_in_under_one_in_ten(setting, capsys):
    """Round 4, the fast guard: the same program on both sides, failing at the same rate, says
    neither use nor worse in 10 % or more of 1000 seeded comparisons. Before the fix a do-nothing
    change read worse in 39 % of them (10 cases x 3 runs, deterministic, 10 % failing)."""
    rates = _flaky_rates(setting, 1000)
    with capsys.disabled():
        print(f"\n{setting} (1000 comparisons): " + ", ".join(f"false {a} {r:.3f}" for a, (r, _) in rates.items()))
    for answer, (rate, _) in rates.items():
        assert rate < 0.10, f"{rate:.3f} of 1000 null comparisons said {answer}"


@pytest.mark.slow
@pytest.mark.parametrize("setting", list(FLAKY_SETTINGS))
def test_failures_that_strike_both_sides_alike_say_use_or_worse_in_under_one_in_ten_two_standard_errors_clear(
        setting, capsys):
    """The same on 6000 seeded comparisons per setting, so that 10 % is more than two standard
    errors above each rate."""
    studies = 6000
    rates = _flaky_rates(setting, studies, seed=5)
    with capsys.disabled():
        print(f"\n{setting} ({studies} comparisons): "
              + ", ".join(f"false {a} {r:.4f} +- {se:.4f}" for a, (r, se) in rates.items()))
    for answer, (rate, se) in rates.items():
        assert rate + 2 * se < 0.10, f"{rate:.4f} of {studies} null comparisons said {answer} (2 SE = {2 * se:.4f})"
