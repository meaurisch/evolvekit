"""One run-wide noise estimate (`evaluate/noise.py`), pooled from the runs' own replicates.

Every accessor returns the noise of one side of a comparison: across seeds on a
stage that is not repeatable, paired with the seed's run on the same seed on one
that is (`v_d / 2`). With nothing to pool, every accessor says 0 -- today.
"""

from __future__ import annotations

import math
import random
import statistics

import pytest

from evolvekit.evaluate.noise import Noise
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
