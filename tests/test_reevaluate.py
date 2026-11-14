"""Re-evaluating the best (`search.reevaluate_best_every`, Q5, A2.4b).

Every N rounds the archive's best runs once more at the final stage on a fresh
seed (`REEVALUATION_SEED_BASE + k`), and every view reads its runs pooled:
`reevaluations.jsonl` beside `runs.jsonl`, and one loader, `leaderboard.read_rows`,
which pools, recomputes the ranking score with the run's lambda, then replays the
hold-out veto. With the key at 0 nothing is run and nothing is read differently.
"""

from __future__ import annotations

import json
import random
import statistics

import pytest

from evolvekit.leaderboard import (
    REEVALUATION_SEED_BASE,
    REEVALUATIONS,
    pool_rows,
    rank,
    read_rows,
    replay_veto,
)


def test_pooling_averages_the_runs_and_counts_them():
    rows = [{"id": "a", "score": 10.0, "ranking_score": 10.0, "public_score": 10.0, "private_score": 10.0,
             "kpis": {"cost": -10.0}}]
    extra = [{"id": "a", "prior_runs": 3, "kpis": {"cost": -6.0}, "score": 6.0},
             {"id": "a", "prior_runs": 4, "kpis": {"cost": -11.0}, "score": 11.0}]
    (row,) = pool_rows(rows, extra)
    assert row["score"] == pytest.approx((4 * 9.0 + 11.0) / 5) and row["replicates"] == 5
    assert row["kpis"]["cost"] == pytest.approx((4 * -9.0 - 11.0) / 5)
    assert row["public_score"] == row["score"], "the public score follows the score"
    assert rows[0]["score"] == 10.0, "the row read is not changed in place"


def test_pooling_recomputes_the_ranking_score_and_then_the_veto():
    """Decision 10: pool -> lambda -> veto. With lambda 1 and a private score of 8 the
    pooled public 9.4 ranks at 9.4 - (9.4 - 8) = 8, not at 10 + (9.4 - 10)."""
    rows = [{"id": "a", "score": 10.0, "ranking_score": 8.0, "public_score": 10.0, "private_score": 8.0, "kpis": {}}]
    extra = [{"id": "a", "prior_runs": 1, "kpis": {}, "score": 8.8}]
    (row,) = pool_rows(rows, extra, holdout_penalty=1.0)
    assert row["score"] == pytest.approx(9.4) and row["ranking_score"] == pytest.approx(8.0)
    assert row["generalization_gap"] == pytest.approx(1.4)


def test_rows_without_reevaluations_are_returned_as_they_are():
    rows = [{"id": "a", "score": 1.0, "kpis": {"cost": 1.0}}, {"id": "b", "score": 2.0}]
    pooled = pool_rows(rows, [{"id": "b", "prior_runs": 1, "kpis": {}, "score": 4.0}])
    assert pooled[0] == {**rows[0], "ranking_score": 1.0} and rows[0] == {"id": "a", "score": 1.0, "kpis": {"cost": 1.0}}
    assert all(p is r for p, r in zip(pool_rows(rows, []), rows))


def test_every_row_is_ranked_with_the_runs_lambda_beside_a_pooled_one():
    """A row written at λ = 1 (ranking 8 for a public 10 and a private 8) beside
    a re-evaluated one: both are ranked with the run's λ = 0, as the driver's
    `_rerank` ranks them, not one at each λ."""
    rows = [{"id": "old", "score": 10.0, "ranking_score": 8.0, "public_score": 10.0, "private_score": 8.0},
            {"id": "b", "score": 9.0, "ranking_score": 9.0, "public_score": 9.0, "private_score": 9.0}]
    old, b = pool_rows(rows, [{"id": "b", "prior_runs": 1, "kpis": {}, "score": 9.0}], holdout_penalty=0.0)
    assert old["ranking_score"] == pytest.approx(10.0) and b["ranking_score"] == pytest.approx(9.0)


def _write(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8", newline="\n")


def test_read_rows_replays_the_veto_after_pooling(tmp_path):
    """A row from before the veto (no `veto` key) whose hold-out is below the seed's
    is kept out by `read_rows` exactly as by `replay_veto` -- after pooling."""
    seed = {"id": "g000-c0001", "operator": "human-seed", "generation": 0, "score": 5.0, "ranking_score": 5.0,
            "public_score": 5.0, "private_score": 5.0, "kpis": {"value": 5.0}, "competes": True}
    low = {"id": "g001-c0002", "operator": "param_lhs", "generation": 1, "score": 9.0, "ranking_score": 9.0,
           "public_score": 9.0, "private_score": 4.0, "kpis": {"value": 9.0}, "competes": True}
    good = {"id": "g001-c0003", "operator": "param_lhs", "generation": 1, "score": 7.0, "ranking_score": 7.0,
            "public_score": 7.0, "private_score": 6.0, "kpis": {"value": 7.0}, "competes": True}
    _write(tmp_path / "runs.jsonl", [seed, low, good])
    _write(tmp_path / REEVALUATIONS, [{"id": low["id"], "generation": 2, "seed": 2000, "stage": "full",
                                       "prior_runs": 1, "kpis": {"value": 7.0}, "score": 7.0, "at": "2026-11-13T00:00:00+00:00"}])
    read = read_rows(tmp_path, holdout_penalty=0.0)
    expected = replay_veto(pool_rows([seed, low, good], [json.loads((tmp_path / REEVALUATIONS).read_text())]))
    assert read == expected
    pooled_low = next(r for r in read if r["id"] == low["id"])
    assert pooled_low["score"] == pytest.approx(8.0) and pooled_low["replicates"] == 2
    assert pooled_low["competes"] is False and "holdout below the seed's" in pooled_low["veto"]
    assert [r["id"] for r in rank(read)] == [good["id"], seed["id"]]


def test_read_rows_without_the_file_is_todays_reading(tmp_path):
    seed = {"id": "g000-c0001", "operator": "human-seed", "score": 5.0, "private_score": 5.0}
    old = {"id": "g001-c0002", "score": 9.0, "private_score": 4.0}
    _write(tmp_path / "runs.jsonl", [seed, old])
    assert read_rows(tmp_path) == replay_veto([seed, old])
    assert read_rows(tmp_path, veto=False) == [seed, old]


def test_a_lucky_best_regresses_towards_the_truth_after_three_reevaluations():
    """Eight equal candidates, one run each of sd 1: the best is biased by E[max of 8]
    = 1.4236. Pooled with three fresh runs of mean 0 its bias is 1.4236 / 4 = 0.356;
    the pooled value has variance (Var(max of 8) + 3) / 16 = (0.345 + 3) / 16, sd 0.457,
    so over 5000 trials the SE of the mean is about 0.0065: the band [0.32, 0.39]
    lies more than 5 SE from 0.356 on either side."""
    rng, biases = random.Random(61), []
    for _ in range(5000):
        best = max(rng.gauss(0, 1) for _ in range(8))
        rows = [{"id": "b", "score": best, "ranking_score": best, "kpis": {}}]
        extra = [{"id": "b", "prior_runs": 1 + j, "kpis": {}, "score": rng.gauss(0, 1)} for j in range(3)]
        biases.append(pool_rows(rows, extra)[0]["score"])
    mean, se = statistics.fmean(biases), statistics.stdev(biases) / len(biases) ** 0.5
    print(f"lucky best's bias after three re-evaluations: {mean:.4f} (SE {se:.4f}; derived 0.356)")
    assert 0.32 <= mean <= 0.39


def test_the_seeds_never_collide_with_the_final_checks():
    from evolvekit.harness.plan import CHECK_SEEDS

    assert REEVALUATION_SEED_BASE > max(CHECK_SEEDS)


# -- the noise, the archive and the stop policy -----------------------------------


def test_a_new_run_joins_the_candidates_own_runs_in_the_noise(tmp_path):
    """Welford: three runs 9, 10, 11 (mean 10, var 1) and a fourth of 14 make
    mean 11 and var ((9-11)^2 + (10-11)^2 + 0 + 3^2) / 3 = 14 / 3."""
    from evolvekit.evaluate.noise import Noise
    from evolvekit.evaluate.types import StageOutcome

    noise = Noise(tmp_path / "noise.json")
    noise.observe("full", "a", StageOutcome(stage_id="full", ok=True, kpis={"cost": 10.0}, kpi_var={"cost": 1.0},
                                            vector_kpis={"cost_per_instance": [10.0]},
                                            vector_var={"cost_per_instance": [1.0]}, runs=3, seed_list=(0, 1, 2)))
    noise.pool("full", "a", StageOutcome(stage_id="full", ok=True, kpis={"cost": 14.0},
                                         vector_kpis={"cost_per_instance": [14.0]}, seed_list=(2000,)))
    assert noise.sd_across("full", "cost") == pytest.approx((14 / 3) ** 0.5)
    assert noise.vector_sd("full", "cost_per_instance", 0) == pytest.approx((14 / 3) ** 0.5)
    noise.save()
    again = Noise.load(tmp_path / "noise.json")
    assert again.sd_across("full", "cost") == pytest.approx((14 / 3) ** 0.5)


def test_the_same_run_is_never_pooled_twice(tmp_path):
    """The driver saves the noise before it writes the re-evaluation's entry: a run
    that dies between the two makes the same run, on the same seed, after the
    resume, and the noise -- read back from its file -- must not count it twice."""
    from evolvekit.evaluate.noise import Noise
    from evolvekit.evaluate.types import StageOutcome

    noise = Noise(tmp_path / "noise.json")
    noise.observe("full", "a", StageOutcome(stage_id="full", ok=True, kpis={"cost": 10.0}, kpi_var={"cost": 1.0},
                                            runs=3, seed_list=(0, 1, 2)))
    again = StageOutcome(stage_id="full", ok=True, kpis={"cost": 14.0}, seed_list=(2000,))
    noise.pool("full", "a", again)
    noise.save()
    resumed = Noise.load(tmp_path / "noise.json")
    resumed.pool("full", "a", again)
    assert resumed.sd_across("full", "cost") == pytest.approx((14 / 3) ** 0.5), "one pooled run, not two"
    resumed.pool("full", "a", StageOutcome(stage_id="full", ok=True, kpis={"cost": 11.0}, seed_list=(2001,)))
    assert resumed.sd_across("full", "cost") != pytest.approx((14 / 3) ** 0.5), "another seed is another run"


def test_a_candidate_with_one_run_adds_nothing_to_the_noise(tmp_path):
    """At `seeds: 1` the candidate has no runs of its own in the estimate, and its one
    run was selected as the best of many: paired with a fresh run it would overstate
    the noise (by about 1.7 times the variance for the best of 8), so nothing is added."""
    from evolvekit.evaluate.noise import Noise
    from evolvekit.evaluate.types import StageOutcome

    noise = Noise(tmp_path / "noise.json")
    noise.pool("full", "a", StageOutcome(stage_id="full", ok=True, kpis={"cost": 14.0}, seed_list=(2000,)))
    assert noise.sd_across("full", "cost") == 0.0 and not noise.known("full")


def test_the_archive_re_places_its_members_with_the_pooled_value():
    from evolvekit.candidate import Candidate
    from evolvekit.config import ArchiveConfig
    from evolvekit.search.archive import Archive

    archive = Archive.from_config(ArchiveConfig())
    lucky = Candidate(id="g001-c0002", generation=1, block="", source="", operator="param_lhs", score=12.0, ranking_score=12.0, competes=True,
                      replicates=1)
    other = Candidate(id="g002-c0003", generation=2, block="", source="", operator="param_lhs", score=11.0, ranking_score=11.0, competes=True,
                      replicates=1)
    archive.add(lucky)
    archive.add(other)
    assert archive.elites()[0].id == lucky.id
    lucky.score = lucky.ranking_score = 10.5
    lucky.replicates = 2
    archive.update(lucky)
    assert archive.elites()[0].id == other.id and archive.best.id == other.id


def test_a_reevaluation_moves_the_bar_both_ways_and_is_never_progress():
    from evolvekit.budget import StopPolicy
    from evolvekit.config import StopConfig

    policy = StopPolicy(StopConfig(patience=5, min_big_steps=0), takes_big_steps=False)
    policy.update(10.0)
    policy.update(12.0)
    policy.update(12.0)
    policy.note_big_step()
    policy.note_big_step_applied()
    assert (policy.best, policy.stagnant) == (12.0, 1)
    policy.rebase(11.0)
    assert (policy.best, policy.stagnant) == (11.0, 1), "a lucky best regressed: the bar comes down"
    policy.rebase(13.0)
    assert (policy.best, policy.stagnant) == (13.0, 1), "an unlucky one: the bar goes up"
    assert (policy.big_steps_in_window, policy.big_steps_applied_in_window) == (1, 1)
    assert policy.update(13.0).stagnant_generations == 2, "the pooled best measured again is a flat round"
    assert policy.update(13.5).stagnant_generations == 0, "a real gain over the pooled bar"


def _rows(*specs):
    return [{"id": cid, "generation": g, "operator": "human-seed" if g == 0 else "param_lhs", "score": s,
             "ranking_score": s, "public_score": s, "competes": True, "kpis": {}} for cid, g, s in specs]


def test_a_best_pooled_down_delays_nothing_retroactively():
    """A (12, round 1) is an improvement; run again at round 3 it pools to 10.5.
    Round 1 keeps its improvement and its 12; round 3 ends with the pooled best
    of every row so far (C's 11) as best and bar, without an improvement; a
    later gain is judged against that bar."""
    from evolvekit.economics import series

    rows = _rows(("s", 0, 10.0), ("A", 1, 12.0), ("C", 2, 11.0), ("D", 3, 10.8), ("E", 4, 11.3), ("F", 5, 11.6))
    extra = [{"id": "A", "generation": 3, "prior_runs": 1, "kpis": {}, "score": 9.0}]
    points = {p.generation: p for p in series(rows, epsilon=0.5, reevaluations=extra)}
    assert points[1].improved and points[1].best == 12.0, "earlier rounds keep their values"
    assert not points[2].improved and points[2].best == 12.0
    assert not points[3].improved and points[3].rebased
    assert (points[3].measured_best, points[3].best) == (12.0, 11.0)
    assert not points[4].improved, "11.3 does not clear the bar 11 by epsilon 0.5"
    assert points[5].improved and points[5].best == 11.6
    assert [p.best for p in series(rows, epsilon=0.5)][3] == 12.0, "without the file: today"


def test_a_best_pooled_up_does_not_cancel_an_earlier_real_improvement():
    from evolvekit.economics import series

    rows = _rows(("s", 0, 10.0), ("A", 1, 11.0), ("B", 2, 10.5), ("C", 3, 12.2))
    extra = [{"id": "A", "generation": 2, "prior_runs": 1, "kpis": {}, "score": 13.0}]
    points = {p.generation: p for p in series(rows, [{"generation": 2, "usd": 1.0}], epsilon=0.5,
                                              reevaluations=extra)}
    assert points[1].improved and points[1].best == 11.0
    assert not points[2].improved and points[2].best == 12.0, "pooled up: not a gain"
    assert points[2].usd_since_improvement == 1.0, "the spend since round 1's improvement goes on counting"
    assert not points[3].improved, "12.2 does not clear the pooled bar 12 by 0.5"


# -- the key ------------------------------------------------------------------


def test_reevaluating_needs_a_seed_placeholder(tmp_path, minimal_raw):
    from evolvekit.config import ConfigError, build_config

    raw = dict(minimal_raw)
    raw["evaluate"] = {
        "stages": [{"id": "static", "kind": "builtin-static"},
                   {"id": "full", "kind": "command", "command": "python eval.py {candidate}", "kpis_from": "stdout"}],
        "score": {"objective": "value"},
    }
    raw["search"] = {"reevaluate_best_every": 2}
    with pytest.raises(ConfigError, match=r"reevaluate_best_every.*\{seed\}"):
        build_config(raw, base_dir=tmp_path)
    raw["evaluate"]["stages"][1]["command"] = "python eval.py {candidate} --seed {seed}"
    assert build_config(raw, base_dir=tmp_path).search.reevaluate_best_every == 2
    raw["search"] = {"reevaluate_best_every": -1}
    with pytest.raises(ConfigError, match="reevaluate_best_every"):
        build_config(raw, base_dir=tmp_path)
    raw["search"] = {}
    assert build_config(raw, base_dir=tmp_path).search.reevaluate_best_every == 0


# -- a run -------------------------------------------------------------------

# value = 10 (+1 when x > 0.5) + seed / 1000: repeatable, so every child at or below
# 0.5 is an exact twin of the seed and every later one above it of the first: one
# child competes beside the seed, and it is the best from the round it appears in.
TOY = r'''
import argparse, json
p = argparse.ArgumentParser()
p.add_argument("--seed", type=int, default=0); p.add_argument("--x", type=float, default=0.0)
a = p.parse_args()
print(json.dumps({"value": 10.0 + (1.0 if a.x > 0.5 else 0.0) + a.seed / 1000.0}))
'''


def _toy(tmp_path, every=2, max_full_evals_per_day=1000, toy=TOY, holdout=False, descriptors=None):
    from evolvekit.config import build_config

    (tmp_path / "toy.py").write_text(toy, encoding="utf-8")
    raw = {
        "problem": {"parameters": {"x": {"type": "float", "low": 0.0, "high": 1.0, "default": 0.0}}},
        "evaluate": {
            "stages": [{"id": "static", "kind": "builtin-static"},
                       {"id": "full", "kind": "command", "kpis_from": "stdout",
                        "command": "{python} toy.py --seed {seed} {params}"}],
            "score": {"objective": "value", "direction": "maximize"},
        },
        "search": {"operators": {"param_lhs": 1.0}, "reevaluate_best_every": every},
        "budget": {"max_full_evals_per_day": max_full_evals_per_day},
    }
    if holdout:
        raw["evaluate"]["stages"][1]["private_inputs"] = ["held"]
    if descriptors is not None:
        raw["search"]["archive"] = {"descriptors": descriptors}
    return build_config(raw, base_dir=tmp_path)


def test_the_starting_point_run_again_never_moves_the_veto_reference(tmp_path):
    """Every child is worse than the starting point here, so the seed is the best
    and is run again; its pooled values must not move the hold-out veto's
    reference (its private score as written) or the margin's (its public
    score as written), live, on resume, or in any view."""
    from evolvekit.leaderboard import seed_private, seed_public
    from evolvekit.ledger import read_jsonl
    from evolvekit.search.driver import Driver

    config = _toy(tmp_path, toy=TOY.replace("10.0 + (1.0", "10.0 - (1.0"), holdout=True)
    run_dir = tmp_path / "run"
    driver = Driver(config, run_dir=run_dir)
    driver.run(generations=2)
    (done,) = _reevaluations(run_dir)
    assert driver.best.operator == "human-seed" and done["id"] == driver.best.id
    raw = list(read_jsonl(run_dir / "runs.jsonl"))
    assert (seed_public(raw), seed_private(raw)) == (pytest.approx(10.0), pytest.approx(10.0))
    assert driver.cascade.seed_public == pytest.approx(10.0) and driver.cascade.seed_private == pytest.approx(10.0)
    resumed = Driver(config, run_dir=run_dir)
    resumed.resume()
    assert (resumed.cascade.seed_public, resumed.cascade.seed_private) == (pytest.approx(10.0), pytest.approx(10.0))

    viewed = read_rows(run_dir)
    seed_row = next(r for r in viewed if r["operator"] == "human-seed")
    assert seed_row["score"] == pytest.approx(11.0) and seed_row["private_score"] == pytest.approx(10.0)
    # A row from before the veto, a hold-out of 10.5: below the pooled public 11, not below
    # the reference 10 as written -- it is not kept out.
    legacy = {"id": "g009-c0099", "generation": 2, "operator": "param_lhs", "score": 10.5,
              "public_score": 10.5, "private_score": 10.5}
    from evolvekit.leaderboard import view_rows

    (judged,) = [r for r in view_rows([*raw, legacy], [done]) if r["id"] == legacy["id"]]
    assert "veto" not in judged


def _reevaluations(run_dir):
    path = run_dir / REEVALUATIONS
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.is_file() else []


def test_every_n_generations_the_best_runs_again_on_a_fresh_seed(tmp_path):
    from evolvekit.economics import series
    from evolvekit.ledger import read_jsonl
    from evolvekit.search.driver import Driver
    from evolvekit.status import build_status

    config = _toy(tmp_path)
    run_dir = tmp_path / "run"
    driver = Driver(config, run_dir=run_dir)
    driver.run(generations=4)
    done = _reevaluations(run_dir)
    assert [(r["generation"], r["seed"], r["prior_runs"]) for r in done] == [(2, 2000, 1), (4, 2001, 2)]
    assert {r["id"] for r in done} == {driver.best.id} and driver.best.operator != "human-seed"
    assert done[0]["score"] == pytest.approx(13.0) and done[1]["kpis"]["value"] == pytest.approx(13.001)
    assert set(done[0]) == {"id", "generation", "seed", "stage", "prior_runs", "kpis", "score", "at"}
    pooled = (2 * 12.0 + 13.001) / 3
    best = driver.best
    assert best.score == pytest.approx(pooled) and best.fitness == pytest.approx(pooled) and best.replicates == 3
    assert driver.grid.best.fitness == pytest.approx(pooled)
    assert driver.stop_policy.best == pytest.approx(pooled), "the bar is the pooled best"

    finished = {e["generation"]: e for e in (json.loads(line) for line in (run_dir / "events.jsonl").read_text().splitlines())
                if e["type"] == "generation_finished"}
    assert finished[4]["best_fitness"] == pytest.approx(pooled)
    # Rounds 2 and 4 ended with the best run again (pooled up: 11 -> 12 -> 12.33):
    # never progress, so their flat count goes on from the round before.
    assert best.generation == 1, "the toy's one competing child is bred in round 1"
    assert finished[1]["flat_generations"] == 0
    for g in (2, 3, 4):
        assert finished[g]["flat_generations"] == finished[g - 1]["flat_generations"] + 1, g
    assert driver.stop_policy.stagnant == 3

    status = build_status(run_dir)
    assert status["progress"]["best"]["score"] == pytest.approx(pooled)
    assert status["best"]["replicates"] == 3 and status["best"]["kpis"]["value"] == pytest.approx(pooled)
    by_round = {p["generation"]: p["best_fitness"] for p in status["progress"]["series"]}
    assert by_round[1] == pytest.approx(11.0), "earlier rounds keep the value they had"
    assert by_round[2] == by_round[3] == pytest.approx(12.0) and by_round[4] == pytest.approx(pooled)
    limits = {item["name"]: item for item in status["health"]["limits"]}
    assert limits["stop.patience"]["used"] == 3
    usage = list(read_jsonl(run_dir / "usage.jsonl"))
    shared = series(replay_veto(list(read_jsonl(run_dir / "runs.jsonl"))), usage, reevaluations=done)
    assert [p.best for p in shared] == [p.best for p in driver._economics()], "one series: the driver's and status's"
    assert [p.improved for p in shared] == [True, True, False, False, False]
    raw = {r["id"]: r for r in read_jsonl(run_dir / "runs.jsonl")}
    assert raw[best.id]["score"] == pytest.approx(11.0), "runs.jsonl keeps the row as it was written"


def test_a_resumed_run_reads_its_reevaluations_back(tmp_path):
    from evolvekit.search.driver import Driver

    config = _toy(tmp_path)
    run_dir = tmp_path / "run"
    live = Driver(config, run_dir=run_dir)
    live.run(generations=4)

    resumed = Driver(config, run_dir=run_dir)
    resumed.resume()
    resumed._restore_stop_policy()
    assert resumed.best.id == live.best.id
    assert resumed.best.fitness == pytest.approx(live.best.fitness) and resumed.best.replicates == 3
    assert resumed.grid.best.fitness == pytest.approx(live.grid.best.fitness)
    assert (resumed.stop_policy.best, resumed.stop_policy.stagnant) == (live.stop_policy.best, live.stop_policy.stagnant)
    assert resumed.budget.full_evals_today() == live.budget.full_evals_today(), "re-evaluations count against the cap"

    Driver(config, run_dir=run_dir).run(generations=2)
    done = _reevaluations(run_dir)
    assert [(r["seed"], r["prior_runs"]) for r in done] == [(2000, 1), (2001, 2), (2002, 3)]


def test_a_reevaluation_on_an_auto_kpi_axis_leaves_the_grid_a_resume_rebuilds(tmp_path):
    """The re-evaluation pools the best's `value` in place; the grid's quantile
    edges are cut again with the pooled value, as the resume cuts them."""
    from evolvekit.search.driver import Driver

    config = _toy(tmp_path, descriptors=[{"kpi": "value", "bins": 2}])
    run_dir = tmp_path / "run"
    live = Driver(config, run_dir=run_dir)
    live.run(generations=4)
    assert live.best.replicates == 3
    resumed = Driver(config, run_dir=run_dir)
    resumed.resume()
    assert live.grid.axes[0].edges == resumed.grid.axes[0].edges
    assert live.grid.axes[0].edges == [pytest.approx((10.0 + live.best.kpis["value"]) / 2)], \
        "halfway between the seed and the pooled best"
    assert {m.id: m.cell for m in live.grid.members.values()} == {m.id: m.cell for m in resumed.grid.members.values()}


def test_the_daily_cap_counts_and_stops_a_reevaluation(tmp_path):
    """Re-evaluations are counted against `max_full_evals_per_day` (Q25) and skipped,
    with a line in the log, when it says no."""
    from evolvekit.search.driver import Driver

    config = _toy(tmp_path, every=1)
    run_dir = tmp_path / "run"
    lines: list[str] = []
    driver = Driver(config, run_dir=run_dir, log=lines.append)
    driver.run(generations=1)
    used = driver.budget.full_evals_today()
    assert len(_reevaluations(run_dir)) == 1
    children = sum(1 for r in driver.ledger.runs() if "full" in (r.get("stages_reached") or []))
    assert used == children + 1, "every candidate that reached the final stage, and the re-evaluation"

    capped = _toy(tmp_path, every=1, max_full_evals_per_day=used + 1)
    lines.clear()
    again = Driver(capped, run_dir=run_dir, log=lines.append)
    again.resume()
    assert again.budget.full_evals_today() == used, "the re-evaluation is counted on resume too"
    again.budget.record_full_eval()  # the day's last full evaluation, spent
    again._reevaluate_best(2)
    assert len(_reevaluations(run_dir)) == 1
    assert any("was not run again" in line and "max_full_evals_per_day" in line for line in lines)


def test_off_by_default_nothing_is_reevaluated(tmp_path, monkeypatch):
    """The key at 0 is today: no run, no file, and every reader reads `runs.jsonl` as
    it did (`read_rows` is `replay_veto` of it, the driver's replay the same rows)."""
    from evolvekit.evaluate.cascade import Cascade
    from evolvekit.search.driver import Driver

    def refuse(*_args, **_kwargs):
        raise AssertionError("nothing is re-evaluated with the key at 0")

    monkeypatch.setattr(Cascade, "reevaluate", refuse)
    config = _toy(tmp_path, every=0)
    assert config.search.reevaluate_best_every == 0
    run_dir = tmp_path / "run"
    driver = Driver(config, run_dir=run_dir)
    driver.run(generations=3)
    assert not (run_dir / REEVALUATIONS).exists()
    raw = driver.ledger.runs()
    assert read_rows(run_dir) == replay_veto(raw)
    replayed = driver._replayed_rows(raw)
    assert [r["ranking_score"] for r in replayed] == [r["ranking_score"] for r in raw]
    assert driver._reevaluations == []


def test_confirm_warns_about_the_reevaluation_seeds(tmp_path):
    from evolvekit.confirm import selected_seeds
    from evolvekit.config import StageConfig

    stage = StageConfig(id="full", kind="command", command="x {seed}", seeds=3)
    assert selected_seeds(tmp_path, stage) == {0, 1, 2}
    _write(tmp_path / REEVALUATIONS, [{"id": "a", "seed": 2000}, {"id": "a", "seed": 2001}])
    assert selected_seeds(tmp_path, stage) == {0, 1, 2, 2000, 2001}


def test_the_harness_exports_the_pooled_best(tmp_path):
    """`_best_values` ranks by the pooled rows: a lucky best that regressed below
    another candidate is not the one exported."""
    from evolvekit.harness.execute import _best_values

    rows = [
        {"id": "g000-c0001", "operator": "human-seed", "score": 1.0, "competes": True, "params": {"x": 0.0}},
        {"id": "g001-c0002", "operator": "param_lhs", "score": 12.0, "competes": True, "params": {"x": 0.9}},
        {"id": "g001-c0003", "operator": "param_lhs", "score": 11.0, "competes": True, "params": {"x": 0.7}},
    ]
    _write(tmp_path / "runs.jsonl", rows)
    assert _best_values(tmp_path) == {"x": 0.9}
    _write(tmp_path / REEVALUATIONS, [{"id": "g001-c0002", "generation": 2, "seed": 2000, "stage": "full",
                                       "prior_runs": 1, "kpis": {}, "score": 9.0, "at": "2026-11-13T00:00:00+00:00"}])
    assert _best_values(tmp_path) == {"x": 0.7}
