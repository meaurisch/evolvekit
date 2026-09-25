"""A candidate that is already clearly behind stops costing.

With ten-minute runs on ten instances, a candidate that is three percent behind
the best after four of them will not turn it round on the other six -- and
finishing them costs an hour of solver time to confirm what is already known.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evolvekit.candidate import SEED_OPERATOR, Candidate, splice_block
from evolvekit.config import ConfigError, build_config
from evolvekit.evaluate import Cascade
from evolvekit.evaluate.fanout import Race

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
    assert values[-0.05] == -95.0
    assert values[0.2] <= -95.0, "no better than the worst finished candidate"


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
