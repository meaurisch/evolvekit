"""One run-wide noise estimate (`evaluate/noise.py`), pooled from the runs' own replicates.

Every accessor returns the noise of one side of a comparison: across seeds on a
stage that is not repeatable, paired with the seed's run on the same seed on one
that is (`v_d / 2`). With nothing to pool, every accessor says 0 -- today.
"""

from __future__ import annotations

import json
import math
import random
import statistics

import pytest

from evolvekit.evaluate.noise import CALIBRATION_ID, Noise
from evolvekit.evaluate.types import StageOutcome


def _out(kpis, *, kpi_var=None, vector_var=None, vectors=None, runs=3, ok=True, seed_runs=None, **extra):
    seed_runs = seed_runs or {}
    seeds = tuple(range(len(next(iter(seed_runs.values()))))) if seed_runs else tuple(range(runs))
    return StageOutcome(stage_id="full", ok=ok, kpis=kpis, kpi_var=kpi_var or {},
                        vector_var=vector_var or {}, vector_kpis=vectors or {}, runs=runs,
                        seed_runs=seed_runs, seed_list=seeds, **extra)


def _runs(values):
    """An outcome from per-seed values of `cost`, with its mean and variance
    (no variance from a single run, as `stages._combine` reports it)."""
    kpi_var = {"cost": statistics.variance(values)} if len(values) > 1 else {}
    return _out({"cost": statistics.fmean(values)}, kpi_var=kpi_var,
                seed_runs={"cost": list(values)}, runs=len(values))


def test_the_pooled_variance_is_the_mean_of_each_candidates_own(tmp_path):
    noise = Noise(tmp_path / "noise.json")
    noise.observe("full", "a", _out({"cost": 10.0}, kpi_var={"cost": 4.0}))
    noise.observe("full", "b", _out({"cost": 12.0}, kpi_var={"cost": 16.0}))
    noise.observe("full", "c", _out({"cost": 11.0}, runs=1))  # one run says nothing about noise
    assert noise.sd("full", "cost") == pytest.approx(math.sqrt(10.0))
    assert noise.sd("proxy", "cost") == 0.0 and noise.sd("full", "other") == 0.0


def test_observing_the_same_candidate_again_replaces_it(tmp_path):
    noise = Noise(tmp_path / "noise.json")
    noise.observe("full", "a", _out({"cost": 10.0}, kpi_var={"cost": 4.0}))
    noise.observe("full", "a", _out({"cost": 10.0}, kpi_var={"cost": 4.0}))
    noise.observe("full", "b", _out({"cost": 10.0}, kpi_var={"cost": 16.0}))
    assert noise.sd("full", "cost") == pytest.approx(math.sqrt(10.0)), "not (4 + 4 + 16) / 3"


def test_only_finished_outcomes_with_two_runs_enter_the_estimate(tmp_path):
    noise = Noise(tmp_path / "noise.json")
    noise.observe("full", "f", _out({}, kpi_var={"cost": 9.0}, ok=False))
    noise.observe("full", "r", _out({}, kpi_var={"cost": 9.0}, raced_out="behind"))
    noise.observe("full", "x", _out({}, kpi_var={"cost": 9.0}, abandoned=True))
    assert not noise.known("full") and noise.sd("full", "cost") == 0.0


def test_the_file_round_trips_and_a_broken_one_is_no_noise(tmp_path):
    path = tmp_path / "noise.json"
    noise = Noise(path)
    noise.observe("full", "a", _out({"cost": 10.0}, kpi_var={"cost": 4.0}))
    noise.save()
    assert Noise.load(path).sd("full", "cost") == pytest.approx(2.0)
    path.write_text("{not json", encoding="utf-8")
    assert Noise.load(path).sd("full", "cost") == 0.0
    path.write_text('{"schema": 1, "stages": {"full": {"kpis": {"cost": {"across": 3}}}}}', encoding="utf-8")
    assert Noise.load(path).sd("full", "cost") == 0.0, "a file of the wrong shape is no noise either"
    path.write_text('{"schema": 1, "stages": {"full": {"kpis": {"cost": {"across": '
                    '{"a": {"runs": 3, "mean": 1.0, "var": -4.0}}}}}}}', encoding="utf-8")
    assert Noise.load(path).sd("full", "cost") == 0.0, "a negative variance is no variance"
    assert Noise.load(tmp_path / "missing.json").known("full") is False


def test_the_score_sd_is_of_one_candidates_mean_over_its_runs():
    from evolvekit.config import ScoreConfig

    noise = Noise()
    noise.observe("full", "a", _out({"cost": 10.0, "late": 1.0}, kpi_var={"cost": 4.0, "late": 1.0}))
    score = ScoreConfig(objective="cost", direction="minimize", weights={"cost": 1.0, "late": 2.0})
    assert noise.score_sd("full", score, seeds=4) == pytest.approx(math.sqrt(4.0 + 4.0 * 1.0) / 2.0)


def test_the_relative_noise_skips_instances_at_zero():
    noise = Noise()
    noise.observe("full", "a", _out({"cost": 50.0}, kpi_var={"cost": 1.0},
                                    vectors={"cost_per_instance": [100.0, 0.0]},
                                    vector_var={"cost_per_instance": [4.0, 1.0]}))
    assert noise.cv("full", "cost") == pytest.approx(0.02), "sqrt(4 / 100**2); the instance at 0 is left out"
    assert noise.vector_sd("full", "cost_per_instance", 0) == pytest.approx(2.0)
    assert noise.vector_sd("full", "bins_per_instance", 0) is None


def test_vector_variances_are_pooled_per_key_and_element():
    """Decision 3: an evaluator's own vector on a stage without instances is pooled too."""
    noise = Noise()
    for cid, var in (("a", [1.0, 9.0]), ("b", [3.0, 7.0])):
        noise.observe("full", cid, _out({"bins": 5.0}, kpi_var={"bins": 0.0},
                                        vectors={"bins_per_instance": [5.0, 6.0]},
                                        vector_var={"bins_per_instance": var}))
    assert noise.vector_sd("full", "bins_per_instance", 0) == pytest.approx(math.sqrt(2.0))
    assert noise.vector_sd("full", "bins_per_instance", 1) == pytest.approx(math.sqrt(8.0))


def test_on_a_repeatable_stage_the_noise_is_the_paired_one_per_side():
    """Decision 1: value = f(program) + g(seed). Across seeds the spread is g's (large);
    the differences to the seed on the same seeds are nearly constant, so the paired
    noise is small. The accessor returns one side: var_d / 2."""
    noise = Noise()
    g = [0.0, 5.0, -5.0]
    noise.observe("full", "seed", _runs([100 + x for x in g]), is_seed=True)
    noise.observe("full", "a", _runs([99 + x + e for x, e in zip(g, (0.1, -0.1, 0.0))]))
    across = noise.sd("full", "cost")
    noise.set_repeatable("full", True)
    paired = noise.sd("full", "cost")
    assert across == pytest.approx(5.0, rel=0.02), "the seed's and a's spread over seeds, pooled"
    assert paired == pytest.approx(math.sqrt(statistics.variance([0.1, -0.1, 0.0]) / 2))
    assert noise.sd_across("full", "cost") == pytest.approx(across), "still reported"


def test_on_a_repeatable_stage_vectors_are_paired_per_element():
    noise = Noise()
    seed_rows = [[100.0, 10.0], [104.0, 12.0], [96.0, 8.0]]
    child_rows = [[99.0, 10.5], [103.2, 12.5], [95.4, 8.5]]
    for cid, rows, is_seed in (("seed", seed_rows, True), ("a", child_rows, False)):
        columns = list(zip(*rows))
        noise.observe("full", cid, StageOutcome(
            stage_id="full", ok=True, runs=3, seed_list=(0, 1, 2),
            vector_kpis={"cost_per_instance": [statistics.fmean(c) for c in columns]},
            vector_var={"cost_per_instance": [statistics.variance(c) for c in columns]},
            seed_vectors={"cost_per_instance": rows}), is_seed=is_seed)
    noise.set_repeatable("full", True)
    d0 = [c[0] - s[0] for c, s in zip(child_rows, seed_rows)]
    assert noise.vector_sd("full", "cost_per_instance", 0) == pytest.approx(math.sqrt(statistics.variance(d0) / 2))
    assert noise.vector_sd("full", "cost_per_instance", 1) == pytest.approx(0.0, abs=1e-12), "a constant shift"
    assert noise.vector_cv("full", "cost_per_instance") == pytest.approx(
        math.sqrt(statistics.variance(d0) / 2 / 100.0**2 / 2)), "per element over the seed's mean, element 1 is 0"


def test_on_a_repeatable_stage_one_seed_has_no_noise():
    noise = Noise()
    noise.observe("full", "seed", _runs([100.0]), is_seed=True)
    noise.observe("full", "a", _out({"cost": 99.0}, runs=1, seed_runs={"cost": [99.0]}))
    noise.set_repeatable("full", True)
    assert noise.sd("full", "cost") == 0.0, "seeds: 1 on a repeatable evaluator is today's behaviour"


def test_repeatability_and_both_estimates_survive_a_resume(tmp_path):
    noise = Noise(tmp_path / "noise.json")
    noise.observe("full", "seed", _runs([100.0, 105.0, 95.0]), is_seed=True)
    noise.observe("full", "a", _runs([99.1, 103.9, 95.0]))
    noise.set_repeatable("full", True)
    noise.save()
    again = Noise.load(tmp_path / "noise.json")
    assert again.repeatable("full") is True
    assert again.sd("full", "cost") == pytest.approx(noise.sd("full", "cost"))
    assert again.sd_across("full", "cost") == pytest.approx(noise.sd_across("full", "cost"))


def test_a_later_observation_without_a_replicate_replaces_the_earlier_one():
    """The latest observation of an id counts, even a failed one: what it replaced goes."""
    noise = Noise()
    noise.observe("full", "a", _out({"cost": 10.0}, kpi_var={"cost": 4.0}))
    noise.observe("full", "b", _out({"cost": 10.0}, kpi_var={"cost": 16.0}))
    noise.observe("full", "a", _out({}, ok=False))
    assert noise.sd("full", "cost") == pytest.approx(4.0), "only b's 16 is left"
    noise.observe("full", "b", _out({"cost": 10.0}, runs=1))
    assert not noise.known("full")


def test_a_repeatable_element_seen_only_across_has_no_noise_and_an_unseen_one_is_unknown():
    noise = Noise()
    noise.observe("full", "seed", _out({"cost": 50.0}, vectors={"cost_per_instance": [100.0]},
                                       vector_var={"cost_per_instance": [4.0]}), is_seed=True)
    noise.set_repeatable("full", True)
    assert noise.vector_sd("full", "cost_per_instance", 0) == 0.0, "replicated, but no pair yet"
    assert noise.vector_sd("full", "cost_per_instance", 1) is None
    assert noise.vector_sd("full", "bins_per_instance", 0) is None


def test_one_malformed_entry_is_left_out_and_the_rest_kept(tmp_path):
    import json

    path = tmp_path / "noise.json"
    path.write_text(json.dumps({"schema": 1, "stages": {
        "full": {"repeatable": "yes",
                 "kpis": {"cost": {"across": {"a": {"runs": 3, "mean": 10.0, "var": 4.0},
                                              "b": {"runs": 3, "mean": 10.0, "var": "16"}}},
                          "late": "broken"},
                 "vectors": {"cost_per_instance": {"across": {"a": [[100.0, 9.0, 3], [1.0, "x", 3]]}}},
                 "seed": {"seeds": [0, 1], "kpis": {"cost": ["bad", 1.0]}}},
        "proxy": 7}}), encoding="utf-8")
    noise = Noise.load(path)
    assert noise.sd("full", "cost") == pytest.approx(2.0), "b's bad variance costs b, not a"
    assert noise.repeatable("full") is None, "an unreadable repeatability is not measured"
    assert noise.vector_sd("full", "cost_per_instance", 0) == pytest.approx(3.0)
    assert noise.vector_sd("full", "cost_per_instance", 1) is None, "a bad element is unknown"
    assert noise.known("proxy") is False


def test_the_relative_noise_of_a_stage_without_instances_uses_its_scalars():
    noise = Noise()
    noise.observe("full", "a", _out({"cost": 10.0}, kpi_var={"cost": 4.0}))
    noise.observe("full", "b", _out({"cost": 20.0}, kpi_var={"cost": 4.0}))
    noise.observe("full", "z", _out({"cost": 0.0}, kpi_var={"cost": 1.0}))  # no ratio at a mean of 0
    assert noise.cv("full", "cost") == pytest.approx(math.sqrt((4 / 100 + 4 / 400) / 2))
    assert noise.cv("full", "other") == 0.0

    paired = Noise()
    paired.observe("full", "seed", _runs([100.0, 104.0, 96.0]), is_seed=True)
    paired.observe("full", "a", _runs([99.0, 103.2, 95.4]))
    paired.set_repeatable("full", True)
    v_d = statistics.variance([-1.0, -0.8, -0.6])
    assert paired.cv("full", "cost") == pytest.approx(math.sqrt(v_d / 2 / 100.0**2))


def test_a_score_by_levels_has_no_summed_noise():
    from evolvekit.config import LevelConfig, ScoreConfig

    noise = Noise()
    noise.observe("full", "a", _out({"cost": 10.0}, kpi_var={"cost": 4.0}))
    level = LevelConfig(kpi="cost", direction="minimize")
    score = ScoreConfig(objective="cost", direction="minimize", weights={"cost": 1.0}, levels=(level,))
    assert noise.score_sd("full", score, seeds=1) == 0.0


def test_the_status_shows_each_kpis_noise_and_where_it_came_from():
    noise = Noise()
    noise.observe("full", "seed", _runs([100.0, 104.0, 96.0]), is_seed=True)
    noise.observe("full", "a", _runs([99.0, 103.2, 95.4]))
    status = noise.to_status("full")
    assert status["repeatable"] is None and status["source"] == "replicates"
    assert set(status["sd"]) == set(status["sd_across"]) == set(status["cv"]) == {"cost"}
    assert status["sd"]["cost"] == pytest.approx(status["sd_across"]["cost"]) == pytest.approx(noise.sd_across("full", "cost"))
    assert status["cv"]["cost"] == pytest.approx(noise.cv("full", "cost"))
    noise.set_repeatable("full", True)
    again = noise.to_status("full")
    assert again["repeatable"] is True and again["source"] == "paired"
    assert again["sd"]["cost"] < again["sd_across"]["cost"]
    assert Noise().to_status("full") == {"repeatable": None, "source": "none", "sd": {}, "sd_across": {}, "cv": {}}
    calibrated = Noise()
    calibrated.calibrate("full", {"cost": [100.0, 110.0, 90.0]}, instance=None, instances=1)
    assert calibrated.to_status("full")["source"] == "calibration"


def test_the_pooled_variance_is_within_5_percent_of_the_truth_over_4000_candidates():
    """The estimator itself: 4000 candidates, 3 runs each, true sd 2. The pooled
    variance is unbiased; its SE is 4/sqrt(4000) = 0.063 (1.6 %), so 5 % is
    three SE away."""
    rng = random.Random(20261110)
    noise = Noise()
    for i in range(4000):
        mu = rng.uniform(50, 150)
        runs = [rng.gauss(mu, 2.0) for _ in range(3)]
        noise.observe("full", f"c{i}", _out({"cost": statistics.fmean(runs)},
                                            kpi_var={"cost": statistics.variance(runs)}))
    assert noise.sd("full", "cost") ** 2 == pytest.approx(4.0, rel=0.05)


# -- calibration ----------------------------------------------------------------


def test_a_calibration_from_one_instance_sets_the_relative_noise_for_all():
    """Three runs on instance 1 give its relative noise; the stage-level value is a
    mean of 3 independent instances with that relative noise each, so its variance
    is cv² · Σ seed_mean_i² / M²."""
    noise = Noise()
    noise.calibrate("full", {"cost": [100.0, 102.0, 98.0]}, instance=1, instances=3,
                    seed_means={"cost": [50.0, 100.0, 200.0]})
    cv = statistics.stdev([100.0, 102.0, 98.0]) / 100.0
    assert noise.cv("full", "cost") == pytest.approx(cv)
    assert noise.sd("full", "cost") == pytest.approx(math.sqrt(cv**2 * (50**2 + 100**2 + 200**2)) / 3)
    assert noise.vector_sd("full", "cost_per_instance", 1) == pytest.approx(2.0)
    assert noise.vector_sd("full", "cost_per_instance", 0) is None, "measured on instance 1 only"


def test_the_calibrated_variance_is_unbiased_within_3_percent_over_20000_trials():
    """The calibration rule as an estimator: 3 runs with cv 2 % on instance 1 of
    μ = [50, 100, 200]; true one-run variance of the stage mean cv²·Σμ²/M². The
    ratio s²/x̄² is unbiased for cv² up to O(cv²) (~0.04 % here); one trial's
    estimate has relative sd 1 (2 df), so over 20 000 trials SE = 0.71 %:
    ± 3 % is over 4 SE away."""
    rng = random.Random(20261111)
    mu, cv = [50.0, 100.0, 200.0], 0.02
    truth = cv**2 * sum(m * m for m in mu) / 9
    total, trials = 0.0, 20_000
    for _ in range(trials):
        noise = Noise()
        runs = [rng.gauss(mu[1], cv * mu[1]) for _ in range(3)]
        noise.calibrate("full", {"cost": runs}, instance=1, instances=3, seed_means={"cost": mu})
        total += noise.sd("full", "cost") ** 2
    ratio = total / trials / truth
    print(f"calibrated variance / truth over {trials} trials: {ratio:.4f}")
    assert ratio == pytest.approx(1.0, abs=0.03)


def test_real_replicates_replace_the_calibration():
    noise = Noise()
    noise.calibrate("full", {"cost": [100.0, 110.0, 90.0]}, instance=None, instances=1)
    assert noise.sd("full", "cost") == pytest.approx(10.0)
    noise.observe("full", "a", _out({"cost": 10.0}, kpi_var={"cost": 4.0}))
    assert noise.sd("full", "cost") == pytest.approx(2.0)
    assert noise.cv("full", "cost") == pytest.approx(0.2), "sqrt(4) / 10: the calibration's ratio is gone too"


def test_on_a_repeatable_stage_the_calibration_is_reported_but_not_read():
    noise = Noise()
    noise.calibrate("full", {"cost": [100.0, 100.0, 103.0]}, instance=None, instances=1)
    noise.set_repeatable("full", True)
    assert noise.sd("full", "cost") == 0.0 and noise.cv("full", "cost") == 0.0
    assert noise.sd_across("full", "cost") == pytest.approx(statistics.stdev([100.0, 100.0, 103.0]))


def test_a_calibration_survives_a_resume(tmp_path):
    noise = Noise(tmp_path / "noise.json")
    noise.calibrate("full", {"cost": [100.0, 102.0, 98.0]}, instance=0, instances=2,
                    seed_means={"cost": [100.0, 50.0]})
    noise.save()
    again = Noise.load(tmp_path / "noise.json")
    assert noise.sd("full", "cost") > 0
    assert again.sd("full", "cost") == pytest.approx(noise.sd("full", "cost"))
    assert CALIBRATION_ID in json_of(tmp_path / "noise.json")["stages"]["full"]["kpis"]["cost"]["across"]


def json_of(path):
    import json

    return json.loads(path.read_text(encoding="utf-8"))


def test_the_calibration_instance_is_the_one_whose_seed_run_took_the_median_time():
    from evolvekit.config import StageConfig
    from evolvekit.evaluate.noise import check_stage

    stage = StageConfig(id="full", kind="command", command="x {seed}", instances=("a", "b", "c", "d"))
    picked = check_stage(stage, StageOutcome(stage_id="full", ok=True, instance_durations=[3.0, 1.0, 2.0, 4.0]))
    assert picked.instances == ("c",), "sorted 1, 2, 3, 4: position (4 - 1) // 2 = 1 is 2.0, instance c"
    tied = check_stage(stage, StageOutcome(stage_id="full", ok=True, instance_durations=[1.0, 1.0, 1.0, 1.0]))
    assert tied.instances == ("b",), "ties by position"
    whole = StageConfig(id="full", kind="command", command="x {seed}")
    assert check_stage(whole, StageOutcome(stage_id="full", ok=True)) is whole


# -- the seed run again, in a cascade -------------------------------------------

# cost = scale(instance) * (1 + x), plus 3 * seed (repeatable) or fresh noise every
# call (wall-clock). Every call leaves calls/<instance>.<seed>.<pid>. The seed's runs
# take the times `_TOY_TIMES` gives them (the `toy_times` fixture), so i2 is the median
# whatever the machine's load: measured times would let a busy machine reorder them.
TOY = r'''
import argparse, json, os, random
p = argparse.ArgumentParser()
p.add_argument("--instance"); p.add_argument("--x", type=float); p.add_argument("--seed", type=int, default=0)
p.add_argument("--mode")
a = p.parse_args()
os.makedirs("calls", exist_ok=True)
seen = len([n for n in os.listdir("calls") if n.startswith("%s.%d." % (a.instance, a.seed))])
open(os.path.join("calls", "%s.%d.%d" % (a.instance, a.seed, os.getpid())), "w").close()
if a.mode == "flaky" and seen >= 1:
    raise SystemExit("the second run on the same seed crashed")
scale = {"i1": 100.0, "i2": 1000.0, "i3": 500.0}[a.instance]
cost = scale * (1.0 + a.x)
cost += random.gauss(0.0, 2.0) if a.mode == "wall-clock" else 3.0 * a.seed
print(json.dumps({"cost": cost, "vehicles": 4}))
'''

_TOY_TIMES = {"i1": 0.15, "i2": 0.30, "i3": 0.45}


@pytest.fixture(autouse=True)
def toy_times(monkeypatch):
    """The toy's instances take fixed, ordered times: the median-instance choice
    reads these in place of the measured `instance_durations`, so it is the same
    under any load. Other stages (other instance names) are left as measured."""
    import dataclasses

    from evolvekit.evaluate import noise as noise_module

    measured = noise_module._median_index

    def ordered(stage, outcome):
        if stage.instances and set(stage.instances) <= set(_TOY_TIMES) \
                and len(outcome.instance_durations) == len(stage.instances):
            outcome = dataclasses.replace(outcome, instance_durations=[_TOY_TIMES[i] for i in stage.instances])
        return measured(stage, outcome)

    monkeypatch.setattr(noise_module, "_median_index", ordered)


def _toy(tmp_path, mode, *, seeds=1, placeholder=True, instances=True, span=0.5):
    from evolvekit.config import build_config

    (tmp_path / "toy.py").write_text(TOY, encoding="utf-8")
    seed = " --seed {seed}" if placeholder else ""
    stage = {"id": "full", "kind": "command", "kpis_from": "stdout", "seeds": seeds,
             "command": "{python} toy.py --instance " + ("{instance}" if instances else "i2")
             + f" --mode {mode}{seed} {{params}}"}
    if instances:
        stage["instances"] = ["i1", "i2", "i3"]
    return build_config(
        {
            "problem": {"parameters": {"x": {"type": "float", "low": -span, "high": span, "default": 0.0}}},
            "evaluate": {
                "stages": [{"id": "static", "kind": "builtin-static"}, stage],
                "score": {"objective": "cost", "direction": "minimize"},
            },
            "search": {"operators": {"param_lhs": 1.0}},
        },
        base_dir=tmp_path,
    )


def seed_candidate(config):
    from evolvekit.candidate import SEED_OPERATOR, Candidate, splice_block

    block = config.problem.parameters.render_block({"x": 0.0})
    source = splice_block(config.problem.skeleton_source(), block, config.problem.block_start, config.problem.block_end)
    return Candidate(id="g000-c0001", generation=0, block=block, source=source, operator=SEED_OPERATOR)


def _calls(tmp_path):
    found: dict[str, list[int]] = {}
    for path in (tmp_path / "calls").iterdir():
        instance, seed, _ = path.name.split(".")
        found.setdefault(instance, []).append(int(seed))
    return {k: sorted(v) for k, v in sorted(found.items())}


def _cascade(config, tmp_path, *, events=None, lines=None):
    from evolvekit.evaluate import Cascade

    return Cascade(config, work_dir=tmp_path / "work",
                   on_event=(lambda t, **f: events.append({"type": t, **f})) if events is not None else None,
                   log=lines.append if lines is not None else None)


def test_the_check_runs_seed_0_again_on_the_median_instance_and_finds_it_repeatable(tmp_path):
    config = _toy(tmp_path, "repeatable", seeds=1)
    events, lines = [], []
    cascade = _cascade(config, tmp_path, events=events, lines=lines)
    cascade.evaluate_generation([seed_candidate(config)])
    assert cascade.noise.repeatable("full") is True and cascade.noise.repeatable("full/private") is True
    assert _calls(tmp_path) == {"i1": [0], "i2": [0, 0, 1], "i3": [0]}, "seed 0 again (no cache) and seed 1, median instance"
    assert cascade.noise.sd("full", "cost") == 0.0, "paired, seeds: 1: today's behaviour"
    assert cascade.noise.sd_across("full", "cost") > 0, "the calibration is still measured and reported"
    checked = [e for e in events if e["type"] == "noise_checked"]
    assert len(checked) == 1 and checked[0]["stage"] == "full" and checked[0]["instance"] == "i2"
    assert checked[0]["repeatable"] is True and checked[0]["runs"] == 3
    assert lines == ["noise: the seed run again on i2: the evaluator gives the same numbers for the same seed "
                     "(comparisons are paired by seed)"]
    assert not [e for e in events if e["type"] == "eval_started" and e.get("instance") == "i2"][1:], \
        "the extra runs are not evaluations of the search"
    assert json_of(tmp_path / "work" / "noise.json")["stages"]["full"]["repeatable"] is True, "saved"


def test_a_wall_clock_evaluator_is_not_repeatable_and_is_calibrated(tmp_path):
    config = _toy(tmp_path, "wall-clock", seeds=1)
    lines = []
    cascade = _cascade(config, tmp_path, lines=lines)
    cascade.evaluate_generation([seed_candidate(config)])
    assert cascade.noise.repeatable("full") is False
    assert cascade.noise.cv("full", "cost") > 0 and cascade.noise.sd("full", "cost") > 0
    assert cascade.noise.sd("full", "vehicles") == 0.0, "a measure that does not vary has no noise"
    assert len(lines) == 1 and lines[0].startswith("noise: the seed run again on i2: one run varies by about ")
    assert lines[0].endswith(" % (the search's comparisons allow for it)")


def test_under_normalize_baseline_the_calibration_knows_the_percent_element_and_the_raw_scalar(tmp_path):
    """Decision 4 for the calibration: the percent vector's element is the raw one
    scaled by 100 / ref_i, and `<obj>_raw` has a raw scalar beside the percent one."""
    config = _toy(tmp_path, "wall-clock", seeds=1)  # instances, normalize: baseline (the default)
    cascade = _cascade(config, tmp_path)
    cascade.evaluate_generation([seed_candidate(config)])
    noise, ref = cascade.noise, cascade.reference["full"]["i2"]
    raw = noise.vector_sd("full", "cost_per_instance", 1)
    assert raw is not None and raw > 0
    assert noise.vector_sd("full", "cost_pct_of_baseline", 1) == pytest.approx(raw * 100.0 / ref)
    assert noise.vector_sd("full", "cost_pct_of_baseline", 0) is None, "measured on i2 only"
    cv = noise.cv("full", "cost")  # the relative noise measured on i2
    means = list(cascade.reference["full"].values())  # the seed's raw means: its reference
    assert noise.sd("full", "cost_raw") == pytest.approx(math.sqrt(cv**2 * sum(x * x for x in means)) / 3), \
        "raw units, all three instances"
    assert noise.sd("full", "cost") == pytest.approx(math.sqrt(cv**2 * 3 * 100.0**2) / 3), \
        "percent: every instance's seed mean is 100"
    assert "cost_raw_per_instance" not in json_of(tmp_path / "work" / "noise.json")["stages"]["full"]["vectors"]


def test_a_stage_with_seeds_only_runs_the_repeat(tmp_path):
    config = _toy(tmp_path, "repeatable", seeds=3)
    cascade = _cascade(config, tmp_path)
    cascade.evaluate_generation([seed_candidate(config)])
    assert _calls(tmp_path)["i2"] == [0, 0, 1, 2], "one extra run: seed 0"
    assert cascade.noise.repeatable("full") is True


def test_without_a_seed_placeholder_nothing_is_run_again(tmp_path):
    config = _toy(tmp_path, "repeatable", seeds=1, placeholder=False)
    cascade = _cascade(config, tmp_path)
    cascade.evaluate_generation([seed_candidate(config)])
    assert cascade.noise.repeatable("full") is None and cascade.noise.sd("full", "cost") == 0.0
    assert all(len(v) == 1 for v in _calls(tmp_path).values())


def test_without_instances_the_check_runs_on_the_whole_input_set(tmp_path):
    config = _toy(tmp_path, "wall-clock", seeds=1, instances=False)
    cascade = _cascade(config, tmp_path)
    cascade.evaluate_generation([seed_candidate(config)])
    assert _calls(tmp_path) == {"i2": [0, 0, 1]}
    assert cascade.noise.repeatable("full") is False and cascade.noise.sd("full", "cost") > 0


def test_the_check_runs_once_per_run(tmp_path):
    config = _toy(tmp_path, "repeatable", seeds=1)
    cascade = _cascade(config, tmp_path)
    cascade.evaluate_generation([seed_candidate(config)])
    first = _calls(tmp_path)
    cascade.evaluate_generation([seed_candidate(config)])
    _cascade(config, tmp_path).evaluate_generation([seed_candidate(config)])  # resumed on the same work dir
    assert _calls(tmp_path) == first


def test_a_failed_extra_run_leaves_the_repeatability_unmeasured(tmp_path):
    config = _toy(tmp_path, "flaky", seeds=1)
    lines = []
    cascade = _cascade(config, tmp_path, lines=lines)
    result = cascade.evaluate_generation([seed_candidate(config)])["g000-c0001"]
    assert result.competes, "the seed itself is unharmed"
    assert cascade.noise.repeatable("full") is None and cascade.noise.sd_across("full", "cost") == 0.0
    assert len(lines) == 1 and lines[0].startswith("noise: the seed could not be run again on i2")


def test_the_check_is_skipped_when_the_run_has_less_time_left_than_one_seed_run(tmp_path):
    from evolvekit.evaluate import Cascade

    config = _toy(tmp_path, "repeatable", seeds=1)
    lines = []
    cascade = Cascade(config, work_dir=tmp_path / "work", time_left=lambda: 0.01, log=lines.append)
    cascade.evaluate_generation([seed_candidate(config)])
    assert all(len(v) == 1 for v in _calls(tmp_path).values()), "nothing was run again"
    assert cascade.noise.repeatable("full") is None
    assert len(lines) == 1 and lines[0].startswith("noise: the seed was not run again on i2: 0 s of the run's time")


def test_the_extra_runs_are_not_counted_against_the_daily_cap(tmp_path):
    from evolvekit.budget import BudgetGuard
    from evolvekit.evaluate import Cascade

    config = _toy(tmp_path, "repeatable", seeds=1)
    budget = BudgetGuard(config.budget)
    Cascade(config, work_dir=tmp_path / "work", budget=budget).evaluate_generation([seed_candidate(config)])
    assert budget.full_evals_today() == 1


# -- the hold-out veto's noise margin (A2.1c) ---------------------------------


def _equal_and_worse_veto_rates(tmp_path, shift: float) -> tuple[float, float]:
    """Kept-out rates with and without the margin for a candidate `shift`
    below the seed on the hold-out; both scores are means of 3 runs of sd 1."""
    from evolvekit.evaluate.cascade import holdout_veto

    config = _toy(tmp_path, "repeatable")  # evaluate.holdout_veto is on by default
    assert config.evaluate.holdout_veto is True
    rng = random.Random(7)
    margin = 2.0 / math.sqrt(3)
    kept_out = literal = 0
    for _ in range(20_000):
        seed = statistics.fmean(rng.gauss(0, 1) for _ in range(3))
        mine = statistics.fmean(rng.gauss(shift, 1) for _ in range(3))
        kept_out += holdout_veto(config, mine, seed, margin) is not None
        literal += holdout_veto(config, mine, seed) is not None
    return kept_out / 20_000, literal / 20_000


def test_a_candidate_as_good_as_the_seed_is_kept_out_in_about_8_percent(tmp_path):
    """Both hold-out scores are means of 3 runs of sd 1, so their difference has
    sd sqrt(2/3) and the margin is 2/sqrt(3): P(Z < -sqrt(2)) = 7.86 %.
    20 000 trials: SE 0.19 %; measured 7.95 % (Decision 12)."""
    kept_out, literal = _equal_and_worse_veto_rates(tmp_path, 0.0)
    assert 0.070 <= kept_out <= 0.088
    assert 0.48 <= literal <= 0.52, "the literal rule keeps out about half"


def test_a_candidate_clearly_worse_on_the_hold_out_is_still_kept_out_in_over_97_percent(tmp_path):
    """Truly worse by 3 (one run's sd is 1): mine - seed is N(-3, 0.816), the margin
    1.155: P(diff < -1.155) = Phi(2.26) = 98.8 %. 20 000 trials: SE 0.08 %."""
    kept_out, literal = _equal_and_worse_veto_rates(tmp_path, -3.0)
    assert kept_out >= 0.97
    assert literal >= kept_out


# -- improvement and elites on the paired scale (A2.4a, Decision 1) -------------


def _param_candidate(config, cid, x, generation):
    from evolvekit.candidate import Candidate, splice_block

    block = config.problem.parameters.render_block({"x": x})
    source = splice_block(config.problem.skeleton_source(), block, config.problem.block_start, config.problem.block_end)
    return Candidate(id=cid, generation=generation, block=block, source=source, operator="param_lhs")


def _placed(cid, fitness):
    from evolvekit.candidate import Candidate

    return Candidate(id=cid, generation=1, block="", source="", operator="x", score=fitness,
                     ranking_score=fitness, kpis={"complexity": 10.0})


def _one_cell():
    from evolvekit.config import ArchiveConfig, DescriptorConfig
    from evolvekit.search.archive import Archive

    return Archive.from_config(ArchiveConfig(descriptors=(DescriptorConfig("complexity", 4, 0.0, 40.0),)))


@pytest.mark.parametrize("seeds", [1, 3])
def test_on_a_repeatable_evaluator_a_small_real_gain_counts(tmp_path, seeds):
    """cost = 1000·(1 + x) + 3·seed on one instance: across seeds the spread is
    sigma_across (about 1.7 from the calibration at seeds: 1, 3 at seeds: 3); a child
    0.5 · sigma_across cheaper than its parent is the same program on the same seeds
    plus a constant. Paired, the noise is 0: the child is no twin, it is an
    improvement, and it takes its parent's cell."""
    from evolvekit.budget import StopPolicy
    from evolvekit.config import StopConfig

    config = _toy(tmp_path, "repeatable", seeds=seeds, instances=False)
    cascade = _cascade(config, tmp_path)
    parent = cascade.evaluate_generation([seed_candidate(config)])["g000-c0001"]
    sigma = cascade.noise.sd_across("full", "cost")
    assert sigma > 1.0, "the across-seed spread is measured"
    child = cascade.evaluate_generation([_param_candidate(config, "c", -0.5 * sigma / 1000.0, 1)])["c"]
    assert cascade.noise.repeatable("full") is True
    assert child.behaviour_twin_id is None and child.competes
    assert child.score > parent.score
    policy = StopPolicy(StopConfig(patience=9, epsilon=None))
    policy.noise_sd = cascade.noise.score_sd("full", config.evaluate.score, seeds)
    assert policy.noise_sd == 0.0, "paired: the same seeds give the same offset"
    policy.update(parent.score)
    assert policy.update(child.score).stagnant_generations == 0
    archive = _one_cell()
    archive.noise_sd = policy.noise_sd
    archive.add(_placed("p", parent.score))
    assert archive.add(_placed("c", child.score)).displaced == "p"
    # The across-seed noise would have called the same gain flat.
    across = cascade.noise.sd_across("full", "cost") / math.sqrt(seeds)
    assert child.score - parent.score < across


def test_on_a_wall_clock_evaluator_the_same_gain_is_within_the_noise(tmp_path):
    """cost = 1000·(1 + x) + gauss(0, 2) fresh each call, seeds 3: the stage is not
    repeatable and every rule's margin is the across-seed one (the rates are those of
    the seeded simulations of A2.1c, A2.2 and A2.4a)."""
    config = _toy(tmp_path, "wall-clock", seeds=3, instances=False)
    cascade = _cascade(config, tmp_path)
    cascade.evaluate_generation([seed_candidate(config)])
    assert cascade.noise.repeatable("full") is False
    assert cascade.noise.sd("full", "cost") == pytest.approx(cascade.noise.sd_across("full", "cost"))
    assert cascade.noise.sd("full", "cost") > 0
    assert cascade.noise.score_sd("full", config.evaluate.score, 3) == pytest.approx(
        cascade.noise.sd_across("full", "cost") / math.sqrt(3))


def _noise_is_handed_over(driver, config):
    final = config.final_stage
    expected = driver.cascade.noise.score_sd(final.id, config.evaluate.score, final.seeds)
    assert expected > 0
    assert driver.stop_policy.noise_sd == driver.grid.noise_sd == expected


def test_a_noisy_run_hands_its_noise_to_the_stop_policy_and_the_archive_and_status_counts_as_it_does(tmp_path):
    """A wall-clock toy whose parameter moves the cost by at most 0.2 (one run's
    noise sd is 2, seeds 3): the automatic epsilon is about 0.6, of the order of
    every gain. The noise reaches both rules after a round and after a resume, every
    finished row says how many runs its score is the mean of, and `status`'s flat
    count is the stop policy's (test_status pins that it is not a reading of the
    series with epsilon 0)."""
    from evolvekit.search.driver import Driver
    from evolvekit.status import build_status

    config = _toy(tmp_path, "wall-clock", seeds=3, instances=False, span=1e-4)
    assert config.stop.epsilon is None
    run_dir = tmp_path / "run"
    driver = Driver(config, run_dir=run_dir)
    driver.run(generations=3)
    _noise_is_handed_over(driver, config)
    assert driver.stop_policy.epsilon_now() == pytest.approx(0.5 * driver.stop_policy.noise_sd)
    finished = [r for r in driver.ledger.runs() if "full" in (r.get("stages_reached") or [])]
    assert finished and all(r["replicates"] == 3 for r in finished)
    events = [json.loads(line) for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    last = [e for e in events if e["type"] == "generation_finished"][-1]
    assert last["flat_generations"] == driver.stop_policy.stagnant
    assert last["stop_epsilon"] == pytest.approx(driver.stop_policy.epsilon_now())
    limits = {l["name"]: l for l in build_status(run_dir)["health"]["limits"]}
    assert limits["stop.patience"]["used"] == driver.stop_policy.stagnant

    resumed = Driver(config, run_dir=run_dir)
    resumed.resume()
    _noise_is_handed_over(resumed, config)
    assert resumed.stop_policy.noise_sd == driver.stop_policy.noise_sd, "the estimate is read back"
