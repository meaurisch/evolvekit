"""A candidate that is already clearly behind stops costing.

With ten-minute runs on ten instances, a candidate that is three percent behind
the best after four of them will not turn it round on the other six -- and
finishing them costs an hour of solver time to confirm what is already known.
"""

from __future__ import annotations

import json
import math
import random
import statistics
from pathlib import Path

import pytest

from evolvekit.candidate import SEED_OPERATOR, Candidate, splice_block
from evolvekit.config import ConfigError, build_config
from evolvekit.evaluate import Cascade
from evolvekit.evaluate.fanout import Job, Race, _Board
from evolvekit.evaluate.noise import Noise
from evolvekit.evaluate.types import StageOutcome

# cost = scale * (1 + x); every call leaves a file behind, so runs can be counted.
SOLVER = r'''
import argparse, json, os
p = argparse.ArgumentParser()
p.add_argument("--instance"); p.add_argument("--x", type=float)
a = p.parse_args()
os.makedirs("calls", exist_ok=True)
open(os.path.join("calls", "%s.%s.%d.txt" % (a.instance, a.x, os.getpid())), "w").close()
scale = {"i1": 100.0, "i2": 1000.0, "i3": 500.0, "i4": 2000.0, "i5": 300.0}[a.instance]
print(json.dumps({"cost": scale * (1.0 + a.x)}))
'''
INSTANCES = ["i1", "i2", "i3", "i4", "i5"]


def _config(tmp_path: Path, **stage):
    (tmp_path / "solver.py").write_text(SOLVER, encoding="utf-8")
    return build_config(
        {
            "problem": {"parameters": {"x": {"type": "float", "low": -0.5, "high": 0.5, "default": 0.0}}},
            "evaluate": {
                "stages": [
                    {"id": "static", "kind": "builtin-static"},
                    {"id": "full", "kind": "command", "kpis_from": "stdout", "instances": INSTANCES,
                     "command": "{python} solver.py --instance {instance} {params}", **stage},
                ],
                "score": {"objective": "cost", "direction": "minimize"},
            },
            "search": {"operators": {"param_lhs": 1.0}},
        },
        base_dir=tmp_path,
    )


def _candidate(config, cid: str, x: float, *, seed: bool = False) -> Candidate:
    space = config.problem.parameters
    block = space.render_block({"x": x})
    source = splice_block(config.problem.skeleton_source(), block, config.problem.block_start, config.problem.block_end)
    return Candidate(id=cid, generation=0 if seed else 1, block=block, source=source,
                     operator=SEED_OPERATOR if seed else "param_local")


def _calls(tmp_path: Path, x: float) -> list[str]:
    return sorted(p.name.split(".")[0] for p in (tmp_path / "calls").glob(f"*.{x}.*.txt"))


# -- the rule --------------------------------------------------------------


def test_how_far_behind_is_measured_on_the_instances_both_have():
    race = Race(objective="cost", minimize=True, incumbent={"a": 100.0, "b": 1000.0, "c": 10.0}, after=2, margin_pct=1.0)
    assert race.behind_pct({"a": 103.0, "b": 1010.0}) == (2, pytest.approx(2.0)), "3 % and 1 %: every instance has the same say"
    assert race.behind_pct({"a": 97.0}) == (1, pytest.approx(-3.0))
    assert race.behind_pct({"z": 1.0}) == (0, 0.0)
    higher_is_better = Race(objective="gain", minimize=False, incumbent={"a": 100.0}, after=1, margin_pct=1.0)
    assert higher_is_better.behind_pct({"a": 95.0}) == (1, pytest.approx(5.0))


def test_the_race_reads_only_at_its_looks():
    race = Race(objective="cost", minimize=True, incumbent={f"i{k}": 100.0 for k in range(10)},
                after=4, margin_pct=1.0, instances=10)
    assert race.looks == (4, 7), "after 4, then every ceil(10 / 4) = 3, never at 10 itself"
    behind = {f"i{k}": 110.0 for k in range(5)}
    assert race.decide(behind) is None, "5 is not a look"
    assert race.decide({k: v for k, v in list(behind.items())[:4]}) is not None
    assert Race(objective="cost", minimize=True, incumbent=_flat(5), after=5, margin_pct=1.0, instances=5).looks == ()
    assert Race(objective="cost", minimize=True, incumbent=_flat(5), after=2, margin_pct=1.0, instances=5).looks == (2, 4)


def _flat(n: int) -> dict[str, float]:
    return {f"i{k}": 100.0 for k in range(n)}


def test_a_race_not_told_the_stages_instances_counts_the_incumbents():
    """`instances` left out is not "never race": n is the incumbent's count."""
    race = Race(objective="cost", minimize=True, incumbent=_flat(10), after=4, margin_pct=1.0)
    assert race.population == 10 and race.looks == (4, 7)
    assert race.decide({f"i{k}": 110.0 for k in range(4)}).startswith("raced out after 4 of 10 instances")


def test_the_margin_grows_with_the_noise_and_never_falls_below_margin_pct():
    quiet = Race(objective="cost", minimize=True, incumbent={"a": 1.0}, after=1, margin_pct=1.0, cv=0.001, instances=4)
    noisy = Race(objective="cost", minimize=True, incumbent={"a": 1.0}, after=1, margin_pct=1.0, cv=0.02, instances=4)
    # Decision 18a: 100 * t(0.9975) * sqrt(2) * cv / sqrt(m), the normal's 2.807 at a known cv.
    assert quiet.margin_at([0.5]) == 1.0 and noisy.margin_at([0.5]) == pytest.approx(100 * 2.8070337683 * 2**0.5 * 0.02)
    assert noisy.margin_at([0.0] * 4) == pytest.approx(100 * 2.8070337683 * 2**0.5 * 0.02 / 2), "a mean of 4: half"
    few = Race(objective="cost", minimize=True, incumbent={"a": 1.0}, after=1, margin_pct=1.0, cv=0.02, instances=4, df=2)
    assert few.margin_at([0.5]) == pytest.approx(100 * 14.0890472756 * 2**0.5 * 0.02, rel=1e-5), "Student's t at 2 df"


def test_the_margin_knows_the_spread_between_cases_and_drops_it_at_the_end():
    race = Race(objective="cost", minimize=True, incumbent=_flat(10), after=2, margin_pct=1.0, instances=10)
    diffs = [0.03, -0.03, 0.03, -0.03]
    s = statistics.stdev(diffs)
    assert race.margin_at(diffs) == pytest.approx(200 * s / 2 * math.sqrt(1 - 4 / 10))
    full = Race(objective="cost", minimize=True, incumbent=_flat(4), after=2, margin_pct=1.0, instances=4)
    assert full.margin_at(diffs) == 1.0, "all four in: the factor is 0"


def _settled(value: float) -> StageOutcome:
    return StageOutcome(stage_id="full", ok=True, kpis={"cost": value})


def test_a_look_is_read_once_per_candidate():
    """Two workers: a seed-1 run that lands before the seed-0 pass is done leaves
    `compared` at a look already read; no second verdict there."""
    names = [f"i{k}" for k in range(10)]
    race = Race(objective="cost", minimize=True, incumbent={n: 100.0 for n in names},
                after=4, margin_pct=1.0, instances=10)
    board = _Board([Job("c", Path("c.py"))], 10, [0, 1], race=race, names=names)
    for k in range(4):  # 0.9 % behind on each: within the margin at look 4
        assert board.settle("c", k, 0, names[k], _settled(100.9)) is None
    # i0's seed 1 lands early: its mean is now 2 % behind, the four 1.175 % --
    # out, were look 4 read again.
    assert race.decide({"i0": 102.0, "i1": 100.9, "i2": 100.9, "i3": 100.9}) is not None
    assert board.settle("c", 0, 1, "i0", _settled(103.1)) is None, "look 4 was read"
    for k in (4, 5):
        assert board.settle("c", k, 0, names[k], _settled(105.0)) is None, "5 and 6 are no looks"
    assert board.settle("c", 6, 0, "i6", _settled(105.0)).startswith("raced out after 7 of 10 instances")
    assert board.margin("c") == pytest.approx(race.margin_at(
        [0.02, 0.009, 0.009, 0.009, 0.05, 0.05, 0.05]), abs=0.005)


def _raced_out(race: Race, values: list[float]) -> bool:
    """One candidate settling its runs one instance after another, through the
    stage's own board: the rule as the stage applies it."""
    names = [f"i{k}" for k in range(len(values))]
    board = _Board([Job("c", Path("c.py"))], race.instances, [0], race=race, names=names)
    return any(board.settle("c", k, 0, names[k], _settled(v)) is not None for k, v in enumerate(values))


def _noisy(rng: random.Random, level: float, cv: float = 0.02, n: int = 10) -> list[float]:
    return [level * (1 + rng.gauss(0, cv)) for _ in range(n)]


def _race(incumbent: list[float], **rule) -> Race:
    rule = {"after": 4, "margin_pct": 1.0, "cv": 0.02, **rule}
    return Race(objective="cost", minimize=True, incumbent={f"i{k}": v for k, v in enumerate(incumbent)},
                instances=len(incumbent), **rule)


def test_an_equal_candidate_against_a_fresh_incumbent_is_raced_out_in_under_1_percent():
    """10 instances, after 4 (looks 4, 7), one run per instance on both sides, cv 2 %:
    the mean ratio over m instances has sd 2.83 %/√m, and the noise term is its
    99.75 % quantile at each look (Decision 18a): 0.25 % a look, about 0.5 % over
    both, less where the spread term raises the margin. 20 000 trials: SE 0.05 %;
    measured 0.47 %."""
    rng, trials = random.Random(21), 20_000
    out = sum(_raced_out(_race(_noisy(rng, 100.0)), _noisy(rng, 100.0)) for _ in range(trials))
    rate = out / trials
    print(f"equal candidate, fresh incumbent: raced out {rate:.2%} (SE {math.sqrt(rate * (1 - rate) / trials):.2%})")
    assert rate < 0.01


def _best_of(rng: random.Random, count: int) -> list[float]:
    return min((_noisy(rng, 100.0) for _ in range(count)), key=statistics.fmean)


def test_an_equal_candidate_against_the_best_of_eight_is_raced_out_in_under_3_percent():
    """The incumbent is the best of eight equal candidates by its mean over 10
    instances: each of its values is biased by 1.42 * 2 % / sqrt(10) = 0.90 %.
    Look 4: z = (3.97 - 0.90) / 1.41 = 2.17; look 7: (2.65 - 0.90) / 1.07 = 1.64 --
    measured 2.73 % (SE 0.12 %). Printed beside it: the best of 50 (about 7 %)."""
    rng, trials = random.Random(22), 20_000
    rates = {}
    for count in (8, 50):
        out = sum(_raced_out(_race(_best_of(rng, count)), _noisy(rng, 100.0)) for _ in range(trials // 4 if count == 50 else trials))
        rates[count] = out / (trials // 4 if count == 50 else trials)
    se = math.sqrt(rates[8] * (1 - rates[8]) / trials)
    print(f"equal candidate vs the best of 8: {rates[8]:.2%} (SE {se:.2%}); vs the best of 50: {rates[50]:.2%}")
    assert rates[8] < 0.03


def test_a_candidate_five_percent_behind_is_raced_out_in_over_80_percent():
    """Behind 5 %, margin 3.97 % at look 4 (sd 1.41 %): P(Z > -0.73) = 77 %; look 7
    (margin 2.65 %, sd 1.07 %) brings it to about 97 % (measured 97.1 %); the spread
    term can only lower it."""
    rng, trials = random.Random(24), 20_000
    out = sum(_raced_out(_race(_noisy(rng, 100.0)), _noisy(rng, 105.0)) for _ in range(trials))
    rate = out / trials
    print(f"five percent behind: raced out {rate:.2%} (SE {math.sqrt(rate * (1 - rate) / trials):.2%})")
    assert rate > 0.80


def test_a_candidate_trading_cases_and_not_behind_on_the_stages_cases_is_raced_out_in_under_10_percent():
    """Decisions 5 and 14. Per-case effects N(0, 3 %) on 10 instances, no run noise
    (cv 0), margin_pct 1, after 4 (looks 4, 7). The race asks whether a candidate is
    behind on *this stage's* cases -- their mean is its score -- so the 10 cases are the
    population and the spread term carries the factor sqrt(1 - m/n). The error is a
    race-out of a candidate whose noise-free mean over all 10 cases is not behind
    (about half the draws). Measured: 3.1 % of those (SE 0.27 %, 4017 of 8000 draws);
    over all draws 19.1 % (SE 0.44 %), 92 % of those race-outs of candidates really
    behind on the 10. Without the spread term the unconditional rate was 30.2 %."""
    rng, trials = random.Random(23), 8000
    out = level = level_out = 0
    for _ in range(trials):
        effects = [rng.gauss(0, 0.03) for _ in range(10)]
        raced = _raced_out(_race([100.0] * 10, cv=0.0), [100.0 * (1 + e) for e in effects])
        out += raced
        if statistics.fmean(effects) <= 0:
            level += 1
            level_out += raced
    rate, overall = level_out / level, out / trials
    print(f"trading cases, not behind on the stage's cases: raced out {rate:.2%} "
          f"(SE {math.sqrt(rate * (1 - rate) / level):.2%}, {level} draws); "
          f"over all draws {overall:.2%} (SE {math.sqrt(overall * (1 - overall) / trials):.2%})")
    assert rate < 0.10


def test_an_instance_at_zero_is_left_out_of_the_relative_noise():
    noise = Noise()
    noise.observe("full", "a", StageOutcome(
        stage_id="full", ok=True, kpis={"cost": 50.0}, kpi_var={"cost": 1.0}, runs=3, seed_list=(0, 1, 2),
        vector_kpis={"cost_per_instance": [100.0, 0.0]}, vector_var={"cost_per_instance": [4.0, 1.0]}))
    cv = noise.vector_cv("full", "cost_per_instance")
    assert cv == pytest.approx(0.02), "sqrt(4 / 100**2): the instance at 0 has no ratio"
    race = Race(objective="cost", minimize=True, incumbent={"a": 100.0, "b": 100.0, "z": 0.0}, after=1,
                margin_pct=1.0, cv=cv, instances=3)
    assert race.behind_pct({"a": 103.0, "z": 50.0}) == (1, pytest.approx(3.0)), "z is skipped"
    assert race.decide({"a": 103.0, "z": 50.0}) is None, "3 % is inside the noise term (7.9 % at one instance)"
    assert race.decide({"a": 110.0, "z": 0.0}).startswith("raced out after 1 of 3 instances: 10.00 %")
    nothing = Race(objective="cost", minimize=True, incumbent={"z": 0.0}, after=1, margin_pct=1.0, instances=2)
    assert nothing.decide({"z": 5.0}) is None, "nothing to compare on"


def test_an_instance_at_zero_is_not_counted_in_the_races_population():
    """Five instances, the incumbent at 0 on one: the race compares on four, so n
    is 4 -- its looks are after 2 and 3 (with n = 5, the look at 4 would read
    every comparable case with the factor still above 0), and with all four in
    the factor is 0."""
    incumbent = {**_flat(4), "i4": 0.0}
    race = Race(objective="cost", minimize=True, incumbent=incumbent, after=2, margin_pct=1.0, instances=5)
    assert race.population == 4 and race.looks == (2, 3)
    diffs = [0.03, -0.03, 0.03, -0.03]
    assert race.margin_at(diffs) == 1.0, "all four comparable cases in: the factor is 0"
    three = {"i0": 103.0, "i1": 101.0, "i2": 105.0, "i4": 50.0}
    s = statistics.stdev([0.03, 0.01, 0.05])
    assert race.margin_at([0.03, 0.01, 0.05]) == pytest.approx(200 * s / math.sqrt(3) * math.sqrt(1 / 4))
    assert race.decide(three).startswith("raced out after 3 of 5 instances: 3.00 % behind"), (
        "the last look, 3 of the 4, is read: 3 % behind, margin 1.15 %"
    )


# -- in a cascade ----------------------------------------------------------


def test_a_candidate_clearly_behind_is_stopped_and_its_other_instances_are_never_run(tmp_path):
    config = _config(tmp_path, race={"after": 2, "margin_pct": 1.0})
    events: list[dict] = []
    cascade = Cascade(config, work_dir=tmp_path / "run" / "work", on_event=lambda t, **f: events.append({"type": t, **f}))
    seed = cascade.evaluate_generation([_candidate(config, "g000-c0001", 0.0, seed=True)])["g000-c0001"]
    assert seed.competes and len(_calls(tmp_path, 0.0)) == 5, "the baseline is a yardstick: it always finishes"

    slow = cascade.evaluate_generation([_candidate(config, "g001-c0002", 0.05)])["g001-c0002"]
    assert _calls(tmp_path, 0.05) == ["i1", "i2"], "three instances never started"
    assert slow.competes is False and slow.last_failure is None, "nothing went wrong"
    assert slow.raced_out == (
        "raced out after 2 of 5 instances: 5.00 % behind the best candidate so far on the same instances (margin 1 %)"
    )
    raced = [e for e in events if e["type"] == "raced_out"]
    assert len(raced) == 1 and raced[0]["candidate_id"] == "g001-c0002" and raced[0]["runs_done"] == 2


def test_with_no_noise_the_rule_is_the_margin_alone(tmp_path):
    """`seeds: 1` without `{seed}`: no noise. After 2 of five instances the looks
    are 2 and 4; the two differences are equal, so the spread adds nothing and the
    margin is `margin_pct` -- the rule as it read before, at its first look."""
    config = _config(tmp_path, race={"after": 2, "margin_pct": 1.0})
    events: list[dict] = []
    cascade = Cascade(config, work_dir=tmp_path / "work", on_event=lambda t, **f: events.append({"type": t, **f}))
    cascade.evaluate_generation([_candidate(config, "g000-c0001", 0.0, seed=True)])
    events.clear()
    slow = cascade.evaluate_generation([_candidate(config, "g001-c0002", 0.05)])["g001-c0002"]
    assert slow.raced_out.endswith("5.00 % behind the best candidate so far on the same instances (margin 1 %)")
    raced = next(e for e in events if e["type"] == "raced_out")
    assert raced["looks"] == [2, 4] and raced["margin_pct"] == 1.0
    started = next(e for e in events if e["type"] == "stage_started" and e["stage"] == "full")
    assert started["race_looks"] == [2, 4]


def test_a_stage_without_a_race_says_no_looks(tmp_path):
    config = _config(tmp_path)
    events: list[dict] = []
    cascade = Cascade(config, work_dir=tmp_path / "work", on_event=lambda t, **f: events.append({"type": t, **f}))
    cascade.evaluate_generation([_candidate(config, "g000-c0001", 0.0, seed=True)])
    assert all("race_looks" not in e for e in events if e["type"] == "stage_started")


def test_the_stage_reports_a_raced_out_candidate_apart_from_its_failures(tmp_path):
    """`stage_finished` counted a raced-out candidate as a failure, so a
    healthy stage that stopped its losers early looked like a broken one."""
    config = _config(tmp_path, race={"after": 2, "margin_pct": 1.0})
    events: list[dict] = []
    cascade = Cascade(config, work_dir=tmp_path / "work", on_event=lambda t, **f: events.append({"type": t, **f}))
    cascade.evaluate_generation([_candidate(config, "g000-c0001", 0.0, seed=True)])
    events.clear()
    cascade.evaluate_generation([_candidate(config, "g001-c0002", 0.05), _candidate(config, "g001-c0003", -0.05)])
    finished = [e for e in events if e["type"] == "stage_finished"]
    assert [(e["candidates"], e["failed"], e["raced_out"]) for e in finished] == [(2, 0, 1)]


def test_the_driver_does_not_learn_from_a_raced_out_candidate_as_if_it_had_finished(tmp_path):
    """A raced-out candidate keeps the score of the stage before -- a
    screening score that may look better than any finished candidate's. The
    race has just shown it is behind; the Parzen estimator must not be told it
    is good."""
    from evolvekit.search.driver import Driver

    config = _config(tmp_path, race={"after": 2, "margin_pct": 1.0})
    driver = Driver(config, run_dir=tmp_path / "run", log=lambda m: None)
    space = config.problem.parameters
    finished = Candidate(id="g001-c0002", generation=1, block=space.render_block({"x": -0.05}), source="",
                         operator="param_local", params={"x": -0.05}, score=-95.0, competes=True,
                         stages_reached=["static", "full"])
    raced = Candidate(id="g001-c0003", generation=1, block=space.render_block({"x": 0.2}), source="",
                      operator="param_local", params={"x": 0.2}, score=-80.0, competes=False,
                      stages_reached=["static", "proxy"], raced_out="raced out after 2 of 5 instances")
    driver.archive = [finished, raced]
    values = {o.values["x"]: o.fitness for o in driver._observations()}
    assert values[0.2] < values[-0.05], "ranked below the worst finished candidate"


def test_the_race_is_against_the_best_so_far_not_against_the_baseline(tmp_path):
    config = _config(tmp_path, race={"after": 2, "margin_pct": 1.0})
    cascade = Cascade(config, work_dir=tmp_path / "work")
    cascade.evaluate_generation([_candidate(config, "g000-c0001", 0.0, seed=True)])
    best = cascade.evaluate_generation([_candidate(config, "g001-c0002", -0.05)])["g001-c0002"]
    assert best.competes and len(_calls(tmp_path, -0.05)) == 5
    assert cascade.incumbent["full"]["id"] == "g001-c0002"

    # Two percent better than the defaults -- and three behind the best.
    behind = cascade.evaluate_generation([_candidate(config, "g002-c0003", -0.02)])["g002-c0003"]
    assert behind.raced_out is not None and len(_calls(tmp_path, -0.02)) == 2
    # Within the margin of the best: it gets to finish.
    close = cascade.evaluate_generation([_candidate(config, "g002-c0004", -0.045)])["g002-c0004"]
    assert close.raced_out is None and close.competes and len(_calls(tmp_path, -0.045)) == 5


def test_the_best_so_far_survives_a_restart(tmp_path):
    config = _config(tmp_path, race={"after": 3, "margin_pct": 0.5})
    first = Cascade(config, work_dir=tmp_path / "work")
    first.evaluate_generation([_candidate(config, "g000-c0001", 0.0, seed=True)])
    first.evaluate_generation([_candidate(config, "g001-c0002", -0.1)])
    held = json.loads((tmp_path / "work" / "incumbent.json").read_text(encoding="utf-8"))
    assert held["full"]["id"] == "g001-c0002" and held["full"]["values"]["i2"] == pytest.approx(900.0)

    resumed = Cascade(config, work_dir=tmp_path / "work")
    result = resumed.evaluate_generation([_candidate(config, "g002-c0003", 0.0 + 0.01)])["g002-c0003"]
    assert result.raced_out is not None and len(_calls(tmp_path, 0.01)) == 3


def test_a_write_of_the_best_so_far_cut_short_does_not_lose_it(tmp_path, monkeypatch):
    """`incumbent.json` was written in place: a run killed during the write
    left half a file, which the next session read as "nothing to race against"
    -- every candidate finished every instance again, silently."""
    config = _config(tmp_path, race={"after": 2, "margin_pct": 1.0})
    first = Cascade(config, work_dir=tmp_path / "work")
    first.evaluate_generation([_candidate(config, "g000-c0001", 0.0, seed=True)])
    first.evaluate_generation([_candidate(config, "g001-c0002", -0.05)])

    write_text = Path.write_text

    def cut_short(self, text, *args, **kwargs):
        if self.name == "incumbent.json":
            write_text(self, text[: len(text) // 2], *args, **kwargs)
            raise KeyboardInterrupt  # the process is killed half-way through the write
        return write_text(self, text, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", cut_short)
    try:
        first.evaluate_generation([_candidate(config, "g002-c0003", -0.1)])
    except KeyboardInterrupt:
        pass
    monkeypatch.undo()
    held = Cascade(config, work_dir=tmp_path / "work").incumbent
    assert held.get("full", {}).get("id") in ("g001-c0002", "g002-c0003")


def test_a_race_that_can_never_stop_anyone_is_said_before_the_run(tmp_path):
    """The race is decided on instances a candidate has finished and a
    candidate still has left; with `after` at or above the number of instances
    that never happens, and the rule did nothing without a word."""
    from evolvekit.preflight import preflight
    from evolvekit.search.driver import Driver

    config = _config(tmp_path, race={"after": 5, "margin_pct": 1.0})
    said = "race.after is 5, but the stage has 5 instance(s): no candidate can ever be raced out"
    assert any(said in w for w in preflight(config).warnings)
    messages: list[str] = []
    Driver(config, run_dir=tmp_path / "run", log=messages.append).run(generations=0)
    assert any(said in m for m in messages), messages
    assert not any("race.after" in w for w in preflight(_config(tmp_path, race={"after": 4})).warnings)


def test_without_a_rule_every_candidate_runs_every_instance(tmp_path):
    config = _config(tmp_path)
    cascade = Cascade(config, work_dir=tmp_path / "work")
    cascade.evaluate_generation([_candidate(config, "g000-c0001", 0.0, seed=True)])
    result = cascade.evaluate_generation([_candidate(config, "g001-c0002", 0.3)])["g001-c0002"]
    assert result.competes and result.raced_out is None and len(_calls(tmp_path, 0.3)) == 5


# -- configuration ---------------------------------------------------------


def test_a_race_needs_instances_to_race_on(tmp_path, minimal_raw):
    minimal_raw["evaluate"]["stages"].append(
        {"id": "full", "kind": "command", "command": "e {candidate} {out}", "race": {"after": 2}}
    )
    with pytest.raises(ConfigError, match=r"race: only applies to a stage that lists `instances`"):
        build_config(minimal_raw, base_dir=tmp_path)


@pytest.mark.parametrize(
    "rule, said",
    [({"margin_pct": 1.0}, r"race: missing required key 'after'|race\.after"), ({"after": 0}, r"race\.after"),
     ({"after": 2, "margin_pct": -1}, r"race\.margin_pct: must be >= 0"), ({"after": 2, "sooner": 1}, "sooner")],
)
def test_a_rule_that_cannot_work_is_a_config_error(tmp_path, rule, said):
    with pytest.raises(ConfigError, match=said):
        _config(tmp_path, race=rule)


# -- what the status document makes of it ----------------------------------


def test_a_raced_out_candidate_is_not_a_failure_and_not_waited_for(tmp_path):
    from evolvekit.status import build_status
    from tests.test_status import NOW, RunDir

    run = RunDir(tmp_path)
    run.started(2000, planned=3)
    run.lock()
    run.heartbeat(1)
    run.event("generation_started", 1500, generation=1)
    run.event("stage_started", 1500, stage="full", private=False, workers=1,
              candidates=["g001-c0002", "g001-c0003"], runs_per_candidate=5)
    common = {"stage": "full", "seed": 0, "private": False, "attempt": 0}
    for cid in ("g001-c0002", "g001-c0003"):
        for instance in ("i1", "i2"):
            run.event("eval_started", 1400, candidate_id=cid, instance=instance, timeout_s=900, **common)
            run.event("eval_finished", 800, candidate_id=cid, instance=instance, ok=True, duration_s=600.0,
                      kpis={"cost": 1.0}, **common)
    run.event("raced_out", 790, candidate_id="g001-c0003", stage="full", private=False, runs_done=2,
              reason="raced out after 2 of 5 instances: 5.00 % behind")
    run.row("g000-c0001", 0, 100.0)
    run.row("g000-c0009", 0, 100.0, operator="param_local", competes=False, raced_out="raced out after 2 of 5 instances")

    document = build_status(tmp_path, now=NOW)
    stage = document["health"]["stage"]
    assert stage["candidates_out"] == 1 and stage["runs_left"] == 3, "only the candidate still racing is waited for"
    assert document["health"]["evaluations"]["failed"] == 0 and document["failures"] == []
    row = next(c for c in document["candidates"] if c["id"] == "g000-c0009")
    assert row["raced_out"].startswith("raced out after 2 of 5") and row["reason"] is None
