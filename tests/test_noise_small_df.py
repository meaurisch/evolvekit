"""Every rule that reads σ̂ holds its documented rate with σ̂ *estimated* (Decision 17b).

The rates the docs quote -- the veto's 8 %, the twin's 84 %, the race's under
1 %, the epsilon's 36 %, the elite's 24 % -- were derived for a known noise. A
study at `seeds: 1` knows its noise from three runs of the seed (2 degrees of
freedom); one at `seeds: 3` from the seed's own three runs at first (2 df
again). With a normal quantile on such an estimate the veto kept out 14.7 % of
equal candidates and the twin rule caught 70 % of twins (statistician round 1).
Each rule now widens its quantile to Student's t at the estimate's df
(`stats.t_scale`); here each rate is measured with σ̂ estimated the way the
product estimates it -- `record_check` on the seed's runs at `seeds: 1`,
`Noise.observe` of the seed's replicates at `seeds: 3` -- and the measured rate
is printed. Every band lies more than 2 SE from the rate measured.

Also here: the race on a repeatable stage at `seeds > 1` reads the incumbent on
the same seeds (17a), and a stage whose repeatability is not measured compares
twins exactly (17d).
"""

from __future__ import annotations

import math
import random
from pathlib import Path
from statistics import fmean, variance

import pytest

from evolvekit.candidate import Candidate
from evolvekit.config import ArchiveConfig, DescriptorConfig, PromoteRule, StageConfig, StopConfig, build_config
from evolvekit.evaluate.fanout import Job, _Board
from evolvekit.evaluate.noise import Noise, record_check
from evolvekit.evaluate.signature import Behaviour, BehaviourIndex, twin_margins, within
from evolvekit.evaluate.types import EvalResult, StageOutcome
from evolvekit.stats import t_quantile, t_scale

MU, SIGMA = 100.0, 2.0
NAMES = [f"i{k}" for k in range(10)]


def _se(p: float, n: int) -> float:
    return math.sqrt(p * (1 - p) / n)


def _sizes(slow_band: tuple[float, float], fast_band: tuple[float, float], *, slow: int = 20_000, fast: int = 4000):
    """Each rate twice: `slow` trials with the tight band (marked slow) and
    `fast` trials in the fast loop, with a band wide enough to stay 2 SE away."""
    return pytest.mark.parametrize(("trials", "band"), [
        pytest.param(slow, slow_band, marks=pytest.mark.slow, id="slow"),
        pytest.param(fast, fast_band, id="fast"),
    ])


def _report(what: str, hits: int, trials: int) -> float:
    rate = hits / trials
    print(f"{what}: {rate:.2%} (SE {_se(rate, trials):.2%}, {trials} trials)")
    return rate


# -- the t quantile ---------------------------------------------------------------


def test_the_t_quantile_inverts_the_t_cdf_and_is_the_normal_at_infinite_df():
    assert t_quantile(0.975, 2) == pytest.approx(4.3027, abs=1e-4)
    assert t_quantile(0.975, 30) == pytest.approx(2.0423, abs=1e-4)
    assert t_quantile(0.025, 5) == pytest.approx(-2.5706, abs=1e-4)
    assert t_quantile(0.975, math.inf) == pytest.approx(1.959964, abs=1e-6)
    assert t_quantile(0.5, 3.5) == 0.0
    assert t_scale(math.sqrt(2), math.inf) == 1.0, "a known sd: the normal rule exactly"
    assert t_scale(math.sqrt(2), 2) == pytest.approx(t_quantile(0.92135, 2) / math.sqrt(2), rel=1e-3)
    assert t_scale(1.0, 2) > t_scale(1.0, 20) > t_scale(1.0, 2000) > 1.0
    with pytest.raises(ValueError):
        t_quantile(1.0, 2)


# -- σ̂ as the product estimates it --------------------------------------------------


def _calibrated(rng: random.Random) -> tuple[Noise, float]:
    """`seeds: 1`, a wall-clock evaluator, no instances: the seed's first run and
    the two extra ones (`record_check`) are all the noise known -- 2 df."""
    stage = StageConfig(id="full", kind="command", command="x {seed}", seeds=1)
    v0 = rng.gauss(MU, SIGMA)
    first = StageOutcome(stage_id="full", ok=True, kpis={"cost": v0}, seed_runs={"cost": [v0]}, seed_list=(0,))
    a, b = rng.gauss(MU, SIGMA), rng.gauss(MU, SIGMA)
    extra = StageOutcome(stage_id="full", ok=True, kpis={"cost": (a + b) / 2}, seed_runs={"cost": [a, b]},
                         seed_list=(0, 1))
    noise = Noise()
    check = record_check(noise, "full", stage, first, extra, objective="cost", digits=9)
    assert check is not None and check.calibrated and not check.repeatable
    return noise, v0


def _runs(values, stage_id="full", private=False) -> StageOutcome:
    return StageOutcome(stage_id=stage_id, ok=True, kpis={"cost": fmean(values)}, kpi_var={"cost": variance(values)},
                        seed_runs={"cost": list(values)}, seed_list=tuple(range(len(values))), runs=len(values),
                        private=private)


def _replicated(rng: random.Random, seeds: int = 3) -> tuple[Noise, float, float]:
    """`seeds: 3`, wall-clock: the seed's three public runs and its three hold-out
    runs, observed as the cascade does; the check found it not repeatable. 2 df each."""
    noise = Noise()
    public = [rng.gauss(MU, SIGMA) for _ in range(seeds)]
    private = [rng.gauss(MU, SIGMA) for _ in range(seeds)]
    noise.observe("full", "seed", _runs(public), is_seed=True)
    noise.observe("full/private", "seed", _runs(private, private=True), is_seed=True)
    for key in ("full", "full/private"):
        noise.set_repeatable(key, False)
    return noise, fmean(public), fmean(private)


def _cascade(tmp_path: Path, seeds: int, *, instances: bool = False, race: bool = False, proxy: bool = False,
             weights: dict[str, float] | None = None):
    from evolvekit.evaluate import Cascade

    full = {"id": "full", "kind": "command", "kpis_from": "stdout", "seeds": seeds,
            "command": "{python} toy.py --seed {seed} {params}"}
    if instances:
        full["instances"] = NAMES
        full["command"] = "{python} toy.py --instance {instance} --seed {seed} {params}"
    if race:
        full["race"] = {"after": 4, "margin_pct": 1.0}
    stages = [{"id": "static", "kind": "builtin-static"}, full]
    if proxy:
        stages.insert(1, {"id": "proxy", "kind": "command", "kpis_from": "stdout", "seeds": seeds,
                          "command": "{python} toy.py --seed {seed} {params}"})
    config = build_config(
        {
            "problem": {"parameters": {"x": {"type": "float", "low": -1.0, "high": 1.0, "default": 0.0}}},
            "evaluate": {"stages": stages, "score": (
                {"objective": "cost", "direction": "minimize"} if weights is None
                else {"objective": next(iter(weights)), "direction": "maximize", "weights": weights})},
            "search": {"operators": {"param_lhs": 1.0}, "novelty": {"behavioural": "on"}},
        },
        base_dir=tmp_path,
    )
    return Cascade(config, work_dir=tmp_path / "work")


# -- the hold-out veto: about 8 % ------------------------------------------------------


def _veto_rate(tmp_path: Path, seeds: int, trials: int, rng: random.Random) -> float:
    from evolvekit.evaluate.cascade import holdout_veto

    cascade = _cascade(tmp_path, seeds)
    kept = 0
    for _ in range(trials):
        if seeds == 1:
            cascade.noise, cascade.seed_public = _calibrated(rng)
            cascade.seed_private = rng.gauss(MU, SIGMA)
            mine = rng.gauss(MU, SIGMA)
        else:
            cascade.noise, cascade.seed_public, cascade.seed_private = _replicated(rng, seeds)
            mine = fmean(rng.gauss(MU, SIGMA) for _ in range(seeds))
        # minimize: holdout_veto compares the scores, which for a minimized
        # objective are the negated costs.
        cascade.seed_public, cascade.seed_private = -cascade.seed_public, -cascade.seed_private
        kept += holdout_veto(cascade.config, -mine, cascade.seed_private, cascade.holdout_margin()) is not None
    return _report(f"veto, equal candidate kept out, seeds: {seeds}, sigma estimated (2 df)", kept, trials)


EIGHT = _sizes((0.070, 0.088), (0.065, 0.095))
"""About 7.9 %: SE 0.19 % at 20 000 trials, 0.43 % at 4000."""


@EIGHT
def test_with_a_calibrated_noise_an_equal_candidate_is_kept_out_in_about_8_percent(tmp_path, trials, band):
    """Statistician round 1: 14.7 % with the normal quantile on a 2-df calibration
    (t_2 theory 14.6 %). With Student's t: P(t_2 < -t_2(Φ(-√2))) = 7.86 %."""
    rate = _veto_rate(tmp_path, 1, trials, random.Random(7))
    assert band[0] <= rate <= band[1]


@EIGHT
def test_at_seeds_3_with_the_seeds_own_replicates_an_equal_candidate_is_kept_out_in_about_8_percent(
        tmp_path, trials, band):
    """The hold-out's own estimate, from the seed's three hold-out runs (2 df)."""
    rate = _veto_rate(tmp_path, 3, trials, random.Random(8))
    assert band[0] <= rate <= band[1]


def _correlated_veto_rate(tmp_path: Path, rho: float, trials: int, rng: random.Random) -> float:
    """score = a + b (weights 1, 1), seeds: 3, not repeatable; one run's a and b
    have sd 1 each and correlation `rho`. The hold-out's estimate from 10
    candidates' runs (`observe`); an equal candidate against the seed."""
    from evolvekit.evaluate.cascade import holdout_veto

    cascade = _cascade(tmp_path, 3, weights={"a": 1.0, "b": 1.0})

    def runs3():
        out = []
        for _ in range(3):
            z1, z2 = rng.gauss(0, 1), rng.gauss(0, 1)
            out.append((z1, rho * z1 + math.sqrt(1 - rho**2) * z2))
        return out

    def outcome(rs):
        a, b = [r[0] for r in rs], [r[1] for r in rs]
        return StageOutcome(stage_id="full", ok=True, kpis={"a": fmean(a), "b": fmean(b)},
                            kpi_var={"a": variance(a), "b": variance(b)}, seed_runs={"a": a, "b": b},
                            seed_list=(0, 1, 2), runs=3, private=True)

    kept = 0
    for _ in range(trials):
        cascade.noise = Noise()
        cascade.noise.set_repeatable("full/private", False)
        for c in range(10):
            cascade.noise.observe("full/private", f"c{c}", outcome(runs3()), is_seed=(c == 0))
        seed, mine = (fmean(x + y for x, y in runs3()) for _ in range(2))
        cascade.seed_public, cascade.seed_private = 1.0, seed
        kept += holdout_veto(cascade.config, mine, seed, cascade.holdout_margin()) is not None
    return _report(f"veto, equal candidate kept out, two measures at correlation {rho}", kept, trials)


@pytest.mark.parametrize("rho", [0.8, 0.0, -0.5])
@_sizes((0.070, 0.088), (0.065, 0.095), slow=20_000, fast=4000)
def test_with_two_correlated_measures_an_equal_candidate_is_kept_out_in_about_8_percent(tmp_path, rho, trials, band):
    """Decision 18b: the score's noise from its own per-run values (Σ w·x per run),
    so the measures' covariance is in. Statistician round 2, with Σ w² sd² and
    Welch-Satterthwaite: 14.8 % at correlation 0.8, 2.3 % at -0.5."""
    rate = _correlated_veto_rate(tmp_path, rho, trials, random.Random(int(10 * rho) + 40))
    assert band[0] <= rate <= band[1]


def _calibrated_correlated_veto_rate(tmp_path: Path, rho: float, trials: int, rng: random.Random) -> float:
    """score = a + b, `seeds: 1`, wall-clock: the only noise is the calibration
    (`record_check` on the seed's three runs); one run's a and b have sd 1 each
    and correlation `rho`. An equal candidate against the seed, one hold-out run
    each; the public noise stands in for the hold-out's, rescaled by 1."""
    from evolvekit.evaluate.cascade import holdout_veto

    cascade = _cascade(tmp_path, 1, weights={"a": 1.0, "b": 1.0})
    stage = StageConfig(id="full", kind="command", command="x {seed}", seeds=1)

    def pair():
        z1, z2 = rng.gauss(0, 1), rng.gauss(0, 1)
        return 100 + z1, 50 + rho * z1 + math.sqrt(1 - rho**2) * z2

    kept = 0
    for _ in range(trials):
        a0, b0 = pair()
        first = StageOutcome(stage_id="full", ok=True, kpis={"a": a0, "b": b0},
                             seed_runs={"a": [a0], "b": [b0]}, seed_list=(0,))
        (a1, b1), (a2, b2) = pair(), pair()
        extra = StageOutcome(stage_id="full", ok=True, kpis={"a": (a1 + a2) / 2, "b": (b1 + b2) / 2},
                             seed_runs={"a": [a1, a2], "b": [b1, b2]}, seed_list=(0, 1))
        cascade.noise = Noise()
        assert record_check(cascade.noise, "full", stage, first, extra, objective="a", digits=9).calibrated
        seed, mine = sum(pair()), sum(pair())
        cascade.seed_public = cascade.seed_private = seed
        kept += holdout_veto(cascade.config, mine, seed, cascade.holdout_margin()) is not None
    return _report(f"veto, equal candidate kept out, calibrated, two measures at correlation {rho}", kept, trials)


@pytest.mark.parametrize("rho", [0.8, 0.0, -0.5])
@_sizes((0.070, 0.088), (0.065, 0.095), slow=20_000, fast=4000)
def test_with_a_calibrated_noise_and_two_correlated_measures_an_equal_candidate_is_kept_out_in_about_8_percent(
        tmp_path, rho, trials, band):
    """Statistician round 3: the calibration kept no per-run values, so a weighted
    score fell back to √(Σ w² sd²): 10.6 / 4.7 / 2.2 % at correlation 0.8 / 0 / -0.5.
    The calibration's three runs are now kept aligned, so the score's own three
    values give its noise (2 df)."""
    rate = _calibrated_correlated_veto_rate(tmp_path, rho, trials, random.Random(int(10 * rho) + 70))
    assert band[0] <= rate <= band[1]


# -- the promotion band: a level candidate falls below it in about 8 % ----------------


def _band_rate(tmp_path: Path, seeds: int, trials: int, rng: random.Random) -> float:
    from evolvekit.evaluate.cascade import select_promoted

    cascade, rule, left = _cascade(tmp_path, seeds), PromoteRule(top_k_per_generation=1), 0
    for _ in range(trials):
        cascade.noise = _calibrated(rng)[0] if seeds == 1 else _replicated(rng, seeds)[0]
        band = cascade._noise_margin("full", seeds)
        ref, mine = (-fmean(rng.gauss(MU, SIGMA) for _ in range(seeds)) for _ in range(2))
        left += "mine" not in select_promoted([("ref", ref), ("mine", mine)], rule, band=band)
    return _report(f"promotion, candidate level with the k-th left out, seeds: {seeds}", left, trials)


@EIGHT
def test_with_a_calibrated_noise_a_candidate_level_with_the_kth_is_left_out_in_about_8_percent(tmp_path, trials, band):
    """It is left out only when it measures more than the band below the k-th:
    P(D < -2·score_sd·t) = 7.86 % at any df (15 % with the normal band at 2 df)."""
    rate = _band_rate(tmp_path, 1, trials, random.Random(9))
    assert band[0] <= rate <= band[1]


@EIGHT
def test_at_seeds_3_a_candidate_level_with_the_kth_is_left_out_in_about_8_percent(tmp_path, trials, band):
    rate = _band_rate(tmp_path, 3, trials, random.Random(10))
    assert band[0] <= rate <= band[1]


# -- behaviour twins: one number in about 84 %, many in at least about 84 % -----------


def _twin_one_number(noise: Noise, seeds: int, rng: random.Random) -> bool:
    x = Behaviour({"cost": fmean(rng.gauss(MU, SIGMA) for _ in range(seeds))}, {})
    y = Behaviour({"cost": fmean(rng.gauss(MU, SIGMA) for _ in range(seeds))}, {})
    margins = twin_margins(x, {"cost": noise.sd("full", "cost")}, {}, seeds,
                           cvs={"cost": noise.cv("full", "cost")}, dfs={"cost": noise.df("full", "cost")},
                           cv_dfs={"cost": noise.cv_df("full", "cost")})
    return within(x, y, margins, 9)


EIGHTY_FOUR = _sizes((0.825, 0.86), (0.815, 0.87))
"""About 84.3 %: SE 0.26 % at 20 000 trials, 0.57 % at 4000."""


@EIGHTY_FOUR
def test_with_a_calibrated_noise_a_true_twin_is_caught_in_about_84_percent(trials, band):
    """Statistician round 1: 70 % with the normal c_1 = √2 on 2 df (t_2 theory 70.7 %).
    With Student's t at the same probability: erf(1) = 84.27 % at any df."""
    rng = random.Random(11)
    caught = sum(_twin_one_number(_calibrated(rng)[0], 1, rng) for _ in range(trials))
    rate = _report("true twin caught, one number, calibrated", caught, trials)
    assert band[0] <= rate <= band[1]


@EIGHTY_FOUR
def test_at_seeds_3_with_the_seeds_own_replicates_a_true_twin_is_caught_in_about_84_percent(trials, band):
    rng = random.Random(12)
    caught = sum(_twin_one_number(_replicated(rng)[0], 3, rng) for _ in range(trials))
    rate = _report("true twin caught, one number, seeds: 3", caught, trials)
    assert band[0] <= rate <= band[1]


def _calibrated_instances(rng: random.Random, scale: list[float], cv: float) -> Noise:
    """`seeds: 1` on 10 instances: the calibration runs on the median instance."""
    m = len(scale)
    stage = StageConfig(id="full", kind="command", command="x {seed} {instance}", seeds=1, instances=tuple(NAMES[:m]))
    row = [s * (1 + rng.gauss(0, cv)) for s in scale]
    first = StageOutcome(stage_id="full", ok=True, kpis={"cost": fmean(row)}, vector_kpis={"cost_per_instance": row},
                         seed_runs={"cost": [fmean(row)]}, seed_vectors={"cost_per_instance": [row]}, seed_list=(0,),
                         instance_durations=[1.0 + k for k in range(m)], instance_names=tuple(NAMES[:m]))
    idx = (m - 1) // 2
    a, b = (scale[idx] * (1 + rng.gauss(0, cv)) for _ in range(2))
    extra = StageOutcome(stage_id="full", ok=True, kpis={"cost": (a + b) / 2},
                         vector_kpis={"cost_per_instance": [(a + b) / 2]}, seed_runs={"cost": [a, b]},
                         seed_vectors={"cost_per_instance": [[a], [b]]}, seed_list=(0, 1),
                         instance_names=(NAMES[idx],))
    noise = Noise()
    assert record_check(noise, "full", stage, first, extra, objective="cost", digits=9).calibrated
    return noise


def _ten_instance_twins(rng: random.Random, trials: int, gain_sd: float) -> int:
    """True twins (`gain_sd` 0) or a real gain of `gain_sd` run-sd on every one
    of 10 instances, under a calibrated noise (one cv, 2 df)."""
    scale = [100.0 * (1 + k) for k in range(10)]
    caught = 0
    for _ in range(trials):
        noise = _calibrated_instances(rng, scale, 0.02)
        name = "cost_per_instance"
        x = Behaviour({}, {name: [s * (1 + rng.gauss(0, 0.02)) for s in scale]})
        y = Behaviour({}, {name: [s * (1 + 0.02 * gain_sd + rng.gauss(0, 0.02)) for s in scale]})
        margins = twin_margins(
            x, {}, {name: [noise.vector_sd("full", name, i) for i in range(10)]}, 1,
            cvs={name: noise.vector_cv("full", name)},
            vector_dfs={name: [noise.vector_df("full", name, i) for i in range(10)]},
            cv_dfs={name: noise.vector_cv_df("full", name)},
        )
        caught += within(x, y, margins, 9)
    return caught


@_sizes((0.825, 1.0), (0.815, 1.0), slow=10_000, fast=2000)
def test_with_a_calibrated_noise_a_true_twin_on_ten_instances_is_caught_in_at_least_about_84_percent(trials, band):
    """Statistician round 1: 55 % on 10 instances (the normal c_10 on 2 df). The ten
    margins rest on one cv, so their misses are positively correlated and the
    catch is above erf(1): "at least about 84 %" (measured 93.8 %)."""
    rate = _report("true twin caught, 10 instances, calibrated", _ten_instance_twins(random.Random(13), trials, 0.0),
                   trials)
    assert band[0] <= rate <= band[1]


def _guarded_twins(rng: random.Random, trials: int, shifts: list[float]) -> int:
    """Through the whole twin check (`BehaviourIndex.check_values`, the "scored at
    least as well" guard included): an earlier-round neighbour at the true level,
    a child off by `shifts[i]` run-sd on case i (minimize: score = -mean cost),
    under a calibrated noise (2 df) on 10 cases."""
    scale, name = [100.0 * (1 + k) for k in range(10)], "cost_per_instance"

    def behaviour(offsets):
        xs = [s * (1 + 0.02 * d) + s * 0.02 * rng.gauss(0, 1) for s, d in zip(scale, offsets)]
        return Behaviour({}, {name: xs}, -fmean(xs))

    twins = 0
    for _ in range(trials):
        noise = _calibrated_instances(rng, scale, 0.02)
        index = BehaviourIndex()
        index.add("old", "full", "h", behaviour([0.0] * 10), generation=1)
        child = behaviour(shifts)
        margins = twin_margins(
            child, {}, {name: [noise.vector_sd("full", name, i) for i in range(10)]}, 1,
            cvs={name: noise.vector_cv("full", name)},
            vector_dfs={name: [noise.vector_df("full", name, i) for i in range(10)]},
            cv_dfs={name: noise.vector_cv_df("full", name)},
        )
        twins += index.check_values("full", child, margins, 9, exclude="child", before=2, exact_digits=9).duplicate
    return twins


@_sizes((0.0, 0.001), (0.0, 0.001), slow=10_000, fast=2000)
def test_with_a_calibrated_noise_a_real_gain_of_four_sd_on_ten_cases_is_a_twin_in_0_percent(trials, band):
    """Decision 18c: the shared estimate lets ten numbers' margins cover a 4-sd move
    together (`within` alone: about 70 %), but a twin must also have scored no
    better than what it resembles: a real gain never is one (statistician round 3:
    0 of 10 000)."""
    rate = _report("real gain of 4 sd on 10 cases called a twin, calibrated",
                   _guarded_twins(random.Random(17), trials, [-4.0] * 10), trials)
    assert band[0] <= rate <= band[1]


@_sizes((0.67, 0.75), (0.65, 0.77), slow=10_000, fast=2000)
def test_with_a_calibrated_noise_a_child_trading_cases_by_four_sd_is_a_twin_in_about_71_percent(trials, band):
    """Equal true score, 4 run-sd better on five cases and 4 worse on the other
    five: the price of one shared 2-df estimate (statistician round 3: 70.7 %).
    Printed beside it: an equal child (about 47 %)."""
    rate = _report("child trading cases by 4 sd called a twin, calibrated",
                   _guarded_twins(random.Random(18), trials, [-4.0] * 5 + [4.0] * 5), trials)
    _report("equal child called a twin, calibrated", _guarded_twins(random.Random(19), trials, [0.0] * 10), trials)
    assert band[0] <= rate <= band[1]


# -- the race: under 1 % against a fresh incumbent ---------------------------------


class _Done:
    def __init__(self, value: float) -> None:
        self.kpis, self.ok, self.stage_id, self.private = {"cost": value}, True, "full", False


def _candidate(cid: str) -> Candidate:
    return Candidate(id=cid, generation=1, block="", source="", operator="param_lhs")


def _instance_outcome(rows: list[list[float]], seeds: list[int]) -> StageOutcome:
    """A per-instance outcome as `fanout._combine` reports it: `rows` per seed."""
    by_i = [[rows[j][i] for j in range(len(seeds))] for i in range(len(NAMES))]
    vec = [fmean(v) for v in by_i]
    many = len(seeds) > 1
    return StageOutcome(
        stage_id="full", ok=True, kpis={"cost": fmean(vec)}, vector_kpis={"cost_per_instance": vec},
        kpi_var={"cost": fmean(variance(v) for v in by_i) / len(NAMES)} if many else {},
        vector_var={"cost_per_instance": [variance(v) for v in by_i]} if many else {},
        seed_runs={"cost": [fmean(r) for r in rows]}, seed_vectors={"cost_per_instance": rows},
        seed_list=tuple(seeds), runs=len(seeds) * len(NAMES), instance_names=tuple(NAMES),
    )


def _race_out(cascade, mine: list[list[float]], seeds: list[int]) -> bool:
    """A challenger through the stage's own race (`Cascade._race_for`) and board,
    its units settled in `run_instance_stage`'s order: seed outer."""
    stage = cascade.config.final_stage
    race = cascade._race_for(stage, [_candidate("c")], private=False)
    board = _Board([Job("c", Path("c.py"))], len(NAMES), seeds, race=race, names=NAMES)
    for j, seed in enumerate(seeds):
        for i, name in enumerate(NAMES):
            if board.settle("c", i, seed, name, _Done(mine[j][i])) is not None:
                return True
    return False


def _calibrated_race_rate(tmp_path: Path, trials: int, rng: random.Random, *, best_of: int = 1,
                          behind: float = 0.0) -> float:
    """`seeds: 1`, cv 2 % per run, 10 cases, a calibrated cv (2 df), through
    `Cascade._race_for`: the incumbent is the best of `best_of` equal
    candidates by its mean, the challenger `behind` (a fraction) worse."""
    cascade = _cascade(tmp_path, 1, instances=True, race=True)
    scale, out = [100.0] * 10, 0
    for _ in range(trials):
        cascade.noise = _calibrated_instances(rng, scale, 0.02)
        cascade.incumbent = {}
        rows = min(([s * (1 + rng.gauss(0, 0.02)) for s in scale] for _ in range(best_of)), key=fmean)
        cascade._note_incumbent(cascade.config.final_stage, _candidate("inc"), _instance_outcome([rows], [0]))
        out += _race_out(cascade, [[s * (1 + behind) * (1 + rng.gauss(0, 0.02)) for s in scale]], [0])
    return _report(f"raced out, calibrated cv, incumbent best of {best_of}, {behind:.0%} behind", out, trials)


@_sizes((0.0, 0.01), (0.0, 0.015), slow=20_000, fast=4000)
def test_with_a_calibrated_cv_an_equal_candidate_is_raced_out_in_under_1_percent(tmp_path, trials, band):
    """Statistician round 1: 4.3 % with the normal noise term on a 2-df cv. The
    noise term is the 99.75 % quantile of the mean ratio's noise at each look,
    Student's t at the cv's df (Decision 18a): 0.25 % a look."""
    rate = _calibrated_race_rate(tmp_path, trials, random.Random(14))
    assert band[0] <= rate < band[1]


@_sizes((0.0, 0.03), (0.0, 0.03), slow=20_000, fast=4000)
def test_with_a_calibrated_cv_an_equal_candidate_against_the_best_of_eight_is_raced_out_in_under_3_percent(
        tmp_path, trials, band):
    rate = _calibrated_race_rate(tmp_path, trials, random.Random(15), best_of=8)
    assert band[0] <= rate < band[1]


@pytest.mark.slow
def test_with_a_calibrated_cv_the_race_stops_only_large_deficits(tmp_path):
    """The power the docs quote at `seeds: 1` (three runs behind the noise): a
    candidate 5 %, 10 % and 20 % behind, 4000 trials each (with the cv known it
    is about 97 % at 5 %, `test_racing`)."""
    rates = {b: _calibrated_race_rate(tmp_path, 4000, random.Random(16), behind=b) for b in (0.05, 0.10, 0.20)}
    assert rates[0.05] < rates[0.10] < rates[0.20] and rates[0.20] > 0.75


def _repeatable_world(rng: random.Random, g: list[list[float]], f: float = 1.0, e: float = 0.002):
    """value(prog, i, s) = scale_i · f · (1 + g[i][s] + e): deterministic given the
    seed but for a small program-by-seed interaction (rev_a2_race_paired)."""
    scale = [100.0 * (1 + k) for k in range(len(NAMES))]
    return [[scale[i] * f * (1 + g[i][s] + rng.gauss(0, e)) for i in range(len(NAMES))] for s in range(len(g[0]))]


def _paired_race_rate(tmp_path: Path, trials: int, rng: random.Random, *, legacy: bool) -> float:
    seeds = [0, 1, 2]
    cascade = _cascade(tmp_path, 3, instances=True, race=True)
    stage = cascade.config.final_stage
    out = 0
    for _ in range(trials):
        g = [[rng.gauss(0, 0.02) for _ in seeds] for _ in NAMES]
        noise = Noise()
        noise.set_repeatable("full", True)
        noise.observe("full", "seed", _instance_outcome(_repeatable_world(rng, g), seeds), is_seed=True)
        for c in range(5):
            noise.observe("full", f"c{c}", _instance_outcome(_repeatable_world(rng, g), seeds))
        cascade.noise, cascade.incumbent = noise, {}
        cascade._note_incumbent(stage, _candidate("inc"), _instance_outcome(_repeatable_world(rng, g), seeds))
        if legacy:
            del cascade.incumbent["full"]["by_seed"]  # an incumbent.json from before the per-seed values
        out += _race_out(cascade, _repeatable_world(rng, g), seeds)
    return _report(f"equal challenger raced out, repeatable, seeds: 3{', legacy incumbent' if legacy else ''}",
                   out, trials)


@_sizes((0.0, 0.01), (0.0, 0.01), slow=4000, fast=1000)
def test_on_a_repeatable_stage_at_seeds_3_an_equal_candidate_is_raced_out_in_under_1_percent(tmp_path, trials, band):
    """Decision 17a. rev_a2_race_paired: the seed-0 prefix against the incumbent's
    means over three seeds with the paired cv (about 0.2 %) raced out 10 % of equal
    challengers. Against the incumbent's own seed-0 values the seed's effect
    cancels and the paired cv is the right noise."""
    rate = _paired_race_rate(tmp_path, trials, random.Random(11), legacy=False)
    assert band[0] <= rate < band[1]


@_sizes((0.0, 0.01), (0.0, 0.01), slow=4000, fast=1000)
def test_with_a_legacy_incumbent_file_the_race_reads_the_across_noise(tmp_path, trials, band):
    """Without the per-seed values, the across cv scaled to √((1 + 1/S)/2): the
    difference of a one-seed prefix and a mean of S seeds is at most
    cv_across²·(1 + 1/S). Conservative on a repeatable stage."""
    rate = _paired_race_rate(tmp_path, trials, random.Random(12), legacy=True)
    assert band[0] <= rate < band[1]


def test_the_incumbent_file_keeps_its_values_per_seed(tmp_path):
    import json

    cascade = _cascade(tmp_path, 3, instances=True, race=True)
    rows = [[float(10 * s + i) + 1.0 for i in range(len(NAMES))] for s in range(3)]
    cascade._note_incumbent(cascade.config.final_stage, _candidate("inc"), _instance_outcome(rows, [0, 1, 2]))
    held = json.loads((tmp_path / "work" / "incumbent.json").read_text(encoding="utf-8"))["full"]
    assert held["by_seed"]["2"]["i3"] == rows[2][3]
    race = cascade._race_for(cascade.config.final_stage, [_candidate("c")], private=False)
    assert race.by_seed[1]["i0"] == rows[1][0]
    assert race.against({"i0": [0], "i1": [0, 1]}) == {"i0": rows[0][0], "i1": fmean([rows[0][1], rows[1][1]])}


# -- the epsilon (about 36 %), the elite rule and the 1/5 rule (about 24 %) -----------


def _score_noise(rng: random.Random, seeds: int, cascade) -> tuple[float, float]:
    noise = _calibrated(rng)[0] if seeds == 1 else _replicated(rng, seeds)[0]
    score = cascade.config.evaluate.score
    return noise.score_sd("full", score, seeds), noise.score_df("full", score)


@pytest.mark.parametrize("seeds", [1, 3])
@_sizes((0.345, 0.385), (0.335, 0.39))
def test_with_the_noise_estimated_an_equal_candidate_reads_as_an_improvement_in_about_36_percent(
        tmp_path, seeds, trials, band):
    """x, b two equal candidates' scores: P(x - b > 0.5·sd·t) = P(Z > 0.354) = 36.2 %
    at any df (40 % with the normal half-sd at 2 df). SE 0.34 % / 0.76 %."""
    from evolvekit.budget import StopPolicy

    cascade, rng, hits = _cascade(tmp_path, seeds), random.Random(51 + seeds), 0
    for _ in range(trials):
        policy = StopPolicy(StopConfig(patience=9, epsilon=None))
        policy.noise_sd, policy.noise_df = _score_noise(rng, seeds, cascade)
        policy.update(-fmean(rng.gauss(MU, SIGMA) for _ in range(seeds)))
        hits += policy.update(-fmean(rng.gauss(MU, SIGMA) for _ in range(seeds))).stagnant_generations == 0
    rate = _report(f"equal candidate read as an improvement, seeds: {seeds}", hits, trials)
    assert band[0] <= rate <= band[1]


@pytest.mark.parametrize("seeds", [1, 3])
@_sizes((0.228, 0.252), (0.215, 0.265))
def test_with_the_noise_estimated_an_equal_challenger_takes_the_cell_in_about_24_percent(tmp_path, seeds, trials, band):
    """P(x - e > sd·t) = P(Z > 0.707) = 24.0 % at any df (29 % with the normal margin
    at 2 df). The 1/5 rule's "improved" reads the same margin (`Archive.margin`).
    SE 0.30 % / 0.68 %."""
    from evolvekit.search.archive import Archive

    cascade, rng, taken = _cascade(tmp_path, seeds), random.Random(61 + seeds), 0
    config = ArchiveConfig(descriptors=(DescriptorConfig("complexity", 4, 0.0, 40.0),))
    for i in range(trials):
        archive = Archive.from_config(config)
        archive.noise_sd, archive.noise_df = _score_noise(rng, seeds, cascade)
        for cid in ("e", "c"):
            score = -fmean(rng.gauss(MU, SIGMA) for _ in range(seeds))
            placed = archive.add(Candidate(id=f"{cid}{i}", generation=1, block="", source="", operator="x",
                                           score=score, ranking_score=score, kpis={"complexity": 10.0}))
        taken += placed.displaced is not None
    rate = _report(f"equal challenger took the cell, seeds: {seeds}", taken, trials)
    assert band[0] <= rate <= band[1]


def test_an_infinite_df_is_todays_rule_in_every_margin():
    """A known sd (`math.inf`): the normal formulas exactly."""
    from evolvekit.budget import StopPolicy
    from evolvekit.search.archive import Archive

    policy = StopPolicy(StopConfig(patience=9, epsilon=None))
    policy.noise_sd = 2.0
    assert policy.epsilon_now() == 1.0
    archive = Archive.from_config(ArchiveConfig(descriptors=(DescriptorConfig("complexity", 4, 0.0, 40.0),)))
    archive.noise_sd = 2.0
    assert archive.margin == 2.0
    margins = twin_margins(Behaviour({"cost": 0.0}, {}), {"cost": 1.0}, {}, seeds=1)
    assert margins["cost"] == pytest.approx(2.0)
    policy.noise_df = archive.noise_df = 2.0
    assert policy.epsilon_now() > 1.0 and archive.margin > 2.0


# -- 17d: a stage whose repeatability is not measured compares twins exactly ---------


def test_at_an_unmeasured_proxy_a_distinct_child_is_a_noise_twin_in_0_percent(tmp_path):
    """rev_a2_proxy_twins: a proxy at `seeds: 3` that is deterministic per seed
    (value = f(prog) + g(seed)), never checked for repeatability, read the
    across-seed sd -- the seed's own effect -- and rejected 91 % of distinct
    children as noise twins of an index of 20. Through the cascade's own twin
    rule: an unmeasured stage compares exactly, and no two of these are equal."""
    cascade = _cascade(tmp_path, 3, proxy=True)
    stage = next(s for s in cascade.config.evaluate.stages if s.id == "proxy")
    rng, trials, rejected = random.Random(5), 1000, 0
    for _ in range(trials):
        g = [rng.gauss(0, 1.0) for _ in range(3)]
        cascade.noise, cascade.signatures = Noise(), BehaviourIndex()

        def note(cid: str, generation: int) -> bool:
            xs = [rng.gauss(0, 1.0) + gs for gs in g]
            outcome = _runs(xs, stage_id="proxy")
            cascade.noise.observe("proxy", cid, outcome, is_seed=(cid == "e0"))
            result = EvalResult(score=fmean(xs), stage_scores={"proxy": fmean(xs)})
            return cascade._note_behaviour(result, outcome, stage, cid, generation=generation)

        for k in range(20):
            note(f"e{k}", 1)
        assert cascade.noise.repeatable("proxy") is None and cascade.noise.sd("proxy", "cost") > 0
        rejected += note("child", 2)
    rate = _report("distinct child rejected as a twin at an unmeasured proxy", rejected, trials)
    assert rate == 0.0
