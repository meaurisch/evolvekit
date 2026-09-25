"""A run that dies in the middle of a generation loses the runs that were in
flight, not the generation.

Two halves. Every successful evaluator run is kept under `work/cache/`, keyed by
what was run (candidate, stage, instance, seed), so running it again is a
lookup. And the children of a generation are written down before they are
evaluated, so the resumed run evaluates *those* children -- whose finished runs
are in the cache -- instead of breeding new ones.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from evolvekit.candidate import SEED_OPERATOR, Candidate, splice_block
from evolvekit.config import build_config
from evolvekit.evaluate import Cascade
from evolvekit.events import read_events

SOLVER = r'''
import argparse, json, os, sys
p = argparse.ArgumentParser()
p.add_argument("--instance"); p.add_argument("--seed", type=int, default=0); p.add_argument("--x", type=float)
a = p.parse_args()
os.makedirs("calls", exist_ok=True)
open(os.path.join("calls", "%s.%s.%d.txt" % (a.instance, a.x, os.getpid())), "w").close()
if os.path.exists("die-on-" + a.instance):
    sys.stderr.write("power cut\n")
    os._exit(9)
print(json.dumps({"cost": 100.0 * (1.0 + a.x) + len(a.instance)}))
'''


def _config(tmp_path: Path, **evaluate):
    (tmp_path / "solver.py").write_text(SOLVER, encoding="utf-8")
    return build_config(
        {
            "problem": {"parameters": {"x": {"type": "float", "low": -0.5, "high": 0.5, "default": 0.0}}},
            "evaluate": {
                "stages": [
                    {"id": "static", "kind": "builtin-static"},
                    {
                        "id": "full", "kind": "command", "kpis_from": "stdout", "timeout": 60,
                        "instances": ["a", "bb", "ccc"],
                        "command": f'"{sys.executable}" solver.py --instance {{instance}} --seed {{seed}} {{params}}',
                    },
                ],
                "score": {"objective": "cost", "direction": "minimize"},
                **evaluate,
            },
            "search": {
                "operators": {"param_local": 1.0}, "children_per_generation": 3, "generations": 2,
                "seed": 2, "novelty": {"behavioural": "off"},
            },
            "budget": {"max_full_evals_per_day": 100},
        },
        base_dir=tmp_path,
    )


def _candidate(config, cid: str, x: float, *, seed: bool = False) -> Candidate:
    space = config.problem.parameters
    block = space.render_block({"x": x})
    source = splice_block(config.problem.skeleton_source(), block, config.problem.block_start, config.problem.block_end)
    return Candidate(id=cid, generation=0 if seed else 1, block=block, source=source,
                     operator=SEED_OPERATOR if seed else "param_local")


def _calls(tmp_path: Path) -> list[str]:
    return sorted(p.name.rsplit(".", 2)[0] for p in (tmp_path / "calls").glob("*.txt"))


# -- the cache -------------------------------------------------------------


def test_a_run_that_was_already_made_is_looked_up_not_made_again(tmp_path):
    config = _config(tmp_path)
    events: list[dict] = []
    first = Cascade(config, work_dir=tmp_path / "run" / "work").evaluate_generation(
        [_candidate(config, "g000-c0001", 0.0, seed=True)]
    )["g000-c0001"]
    assert len(_calls(tmp_path)) == 3

    again = Cascade(config, work_dir=tmp_path / "run" / "work", on_event=lambda t, **f: events.append({"type": t, **f}))
    second = again.evaluate_generation([_candidate(config, "g000-c0001", 0.0, seed=True)])["g000-c0001"]
    assert len(_calls(tmp_path)) == 3, "nothing was run a second time"
    assert second.kpis == first.kpis and second.competes
    finished = [e for e in events if e["type"] == "eval_finished"]
    assert [e["cached"] for e in finished] == [True, True, True]
    assert all(e["duration_s"] == 0.0 for e in finished), "a lookup costs no evaluator time"


def test_the_same_configuration_under_another_id_is_the_same_run(tmp_path):
    config = _config(tmp_path)
    cascade = Cascade(config, work_dir=tmp_path / "work")
    cascade.evaluate_generation([_candidate(config, "g000-c0001", 0.0, seed=True)])
    cascade.evaluate_generation([_candidate(config, "g001-c0002", 0.0), _candidate(config, "g001-c0003", 0.25)])
    assert _calls(tmp_path) == ["a.0.0", "a.0.25", "bb.0.0", "bb.0.25", "ccc.0.0", "ccc.0.25"]


def test_a_failed_run_is_never_cached(tmp_path):
    config = _config(tmp_path)
    (tmp_path / "die-on-bb").write_text("", encoding="utf-8")
    cascade = Cascade(config, work_dir=tmp_path / "work")
    failed = cascade.evaluate_generation([_candidate(config, "g000-c0001", 0.0, seed=True)])["g000-c0001"]
    assert not failed.competes and "instance bb" in failed.last_failure
    (tmp_path / "die-on-bb").unlink()
    retried = cascade.evaluate_generation([_candidate(config, "g000-c0001", 0.0, seed=True)])["g000-c0001"]
    assert retried.competes
    assert _calls(tmp_path).count("a.0.0") == 1 and _calls(tmp_path).count("bb.0.0") == 2


def test_the_cache_can_be_switched_off(tmp_path):
    config = _config(tmp_path, cache=False)
    cascade = Cascade(config, work_dir=tmp_path / "work")
    for _ in range(2):
        cascade.evaluate_generation([_candidate(config, "g000-c0001", 0.0, seed=True)])
    assert len(_calls(tmp_path)) == 6
    assert not (tmp_path / "work" / "cache").exists()


def test_a_changed_command_is_a_different_run(tmp_path):
    config = _config(tmp_path)
    Cascade(config, work_dir=tmp_path / "work").evaluate_generation([_candidate(config, "g000-c0001", 0.0, seed=True)])
    raw_stage = config.evaluate.stages[1]
    from dataclasses import replace

    longer = replace(config, evaluate=replace(config.evaluate, stages=(
        config.evaluate.stages[0], replace(raw_stage, command=raw_stage.command + " --seed 7"),
    )))
    Cascade(longer, work_dir=tmp_path / "work").evaluate_generation([_candidate(longer, "g000-c0001", 0.0, seed=True)])
    assert len(_calls(tmp_path)) == 6


def test_an_instance_file_that_changed_is_a_different_run(tmp_path):
    """The key named the instance, not what was in it: regenerate `f01.json`
    under the same name and `confirm` or a resumed run served the old results
    as if they had just been measured."""
    config = _config(tmp_path)
    for name in ("a", "bb", "ccc"):
        (tmp_path / name).write_text(f"instance {name}\n", encoding="utf-8")
    cascade = Cascade(config, work_dir=tmp_path / "work")
    cascade.evaluate_generation([_candidate(config, "g000-c0001", 0.0, seed=True)])
    (tmp_path / "bb").write_text("instance bb, regenerated with more clients\n", encoding="utf-8")
    Cascade(config, work_dir=tmp_path / "work").evaluate_generation([_candidate(config, "g000-c0001", 0.0, seed=True)])
    calls = _calls(tmp_path)
    assert calls.count("bb.0.0") == 2, "the changed instance was not run again"
    assert calls.count("a.0.0") == 1 and calls.count("ccc.0.0") == 1, "the unchanged ones were"


def test_a_solver_script_that_changed_is_a_different_run(tmp_path):
    config = _config(tmp_path)
    Cascade(config, work_dir=tmp_path / "work").evaluate_generation([_candidate(config, "g000-c0001", 0.0, seed=True)])
    with (tmp_path / "solver.py").open("a", encoding="utf-8") as handle:
        handle.write("# a fix to the solver\n")
    Cascade(config, work_dir=tmp_path / "work").evaluate_generation([_candidate(config, "g000-c0001", 0.0, seed=True)])
    assert len(_calls(tmp_path)) == 6


def test_a_result_slower_than_todays_timeout_is_not_reused(tmp_path):
    """A success is only an answer under a timeout it fits in. Raising the
    timeout keeps the cache; lowering it below what a run took must not."""
    from dataclasses import replace

    config = _config(tmp_path)
    Cascade(config, work_dir=tmp_path / "work").evaluate_generation([_candidate(config, "g000-c0001", 0.0, seed=True)])
    for entry in (tmp_path / "work" / "cache").glob("*.json"):
        payload = json.loads(entry.read_text(encoding="utf-8"))
        entry.write_text(json.dumps({**payload, "ran_for_s": 50.0}), encoding="utf-8")

    def with_timeout(seconds: float):
        stage = config.evaluate.stages[1]
        return replace(config, evaluate=replace(config.evaluate, stages=(
            config.evaluate.stages[0], replace(stage, timeout=seconds))))

    longer = with_timeout(120.0)
    Cascade(longer, work_dir=tmp_path / "work").evaluate_generation([_candidate(longer, "g000-c0001", 0.0, seed=True)])
    assert len(_calls(tmp_path)) == 3, "a longer timeout: the 50 s results still stand"
    shorter = with_timeout(30.0)
    Cascade(shorter, work_dir=tmp_path / "work").evaluate_generation([_candidate(shorter, "g000-c0001", 0.0, seed=True)])
    assert len(_calls(tmp_path)) == 6, "a 30 s timeout: a 50 s result would have timed out"


# -- the generation that was interrupted -----------------------------------


@pytest.mark.slow
def test_a_run_killed_mid_generation_resumes_with_the_same_children_and_pays_only_for_what_was_lost(tmp_path):
    """Generation 1 is killed from inside (the driver dies with the second
    instance of its first child); the resumed run has to finish *that*
    generation, with the children it had already bred."""
    from evolvekit.search.driver import Driver

    config = _config(tmp_path)
    driver = Driver(config, run_dir=tmp_path / "run", log=lambda m: None)
    real = driver.cascade.evaluate_generation
    state = {"generation": 0}

    def dying(candidates, scores=()):
        state["generation"] += 1
        if state["generation"] == 2:  # generation 1: let a few runs finish, then die
            driver.cascade.evaluate_generation = real
            original = driver.cascade.on_event
            seen = {"finished": 0}

            def counting(type, **fields):
                original(type, **fields)
                if type == "eval_finished":
                    seen["finished"] += 1
                    if seen["finished"] == 4:
                        raise KeyboardInterrupt

            driver.cascade.on_event = counting
        return real(candidates, scores)

    driver.cascade.evaluate_generation = dying
    with pytest.raises(KeyboardInterrupt):
        driver.run()

    pending = json.loads((tmp_path / "run" / "pending.json").read_text(encoding="utf-8"))
    assert pending["generation"] == 1 and len(pending["children"]) == 3
    bred = {c["id"]: c["params"] if c.get("params") else c["block"] for c in pending["children"]}
    calls_before = len(_calls(tmp_path))
    assert calls_before >= 3 + 4

    resumed = Driver(config, run_dir=tmp_path / "run", log=lambda m: None)
    summary = resumed.run()
    rows = resumed.ledger.runs()
    first_generation = [r for r in rows if r["generation"] == 1]
    assert [r["id"] for r in first_generation] == sorted(bred), "the children that were bred, not new ones"
    assert not (tmp_path / "run" / "pending.json").exists()
    assert summary.generations == 2 and {r["generation"] for r in rows} == {0, 1, 2}

    cached = [e for e in read_events(tmp_path / "run") if e["type"] == "eval_finished" and e.get("cached")]
    assert len(cached) >= 4, "what had finished before the kill was not run again"
    adopted = [e for e in read_events(tmp_path / "run") if e["type"] == "generation_adopted"]
    assert adopted and adopted[0]["generation"] == 1 and adopted[0]["children"] == 3
