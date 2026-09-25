"""A stage that lists `instances` runs once per instance: spread over pinned
workers, retried one run at a time, reported under the instance's name, and
combined so that every instance has the same say.

The stand-in solver below is what a user would wrap: it takes an instance, a
seed and flags, writes one JSON object, and knows nothing about evolvekit.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

from evolvekit.candidate import SEED_OPERATOR, Candidate, splice_block
from evolvekit.config import ConfigError, build_config
from evolvekit.evaluate import Cascade, build_argv
from evolvekit.evaluate.process import can_pin, run_bounded

SOLVER = r'''
import argparse, json, os, sys, time
p = argparse.ArgumentParser()
p.add_argument("--instance"); p.add_argument("--seed", type=int, default=0); p.add_argument("--out")
p.add_argument("--small", type=float, default=0.0); p.add_argument("--big", type=float, default=0.0)
a = p.parse_args()
started = time.time()
spec = json.load(open(a.instance))
name = os.path.splitext(os.path.basename(a.instance))[0]
if spec.get("flaky"):
    marker = "flaky.%s.%d.marker" % (name, a.seed)
    if not os.path.exists(marker):
        open(marker, "w").close()
        sys.stderr.write("segfault, or so\n")
        sys.exit(3)
if spec.get("crash"):
    sys.stderr.write("cannot solve " + name + "\n")
    sys.exit(3)
time.sleep(spec.get("sleep", 0.0))
gain = a.small if name.startswith("s") else a.big
cost = spec["scale"] * (1.0 + gain) + 0.5 * a.seed
if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32")
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.GetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(ctypes.c_size_t)]
    mine, system = ctypes.c_size_t(), ctypes.c_size_t()
    k32.GetProcessAffinityMask(k32.GetCurrentProcess(), ctypes.byref(mine), ctypes.byref(system))
    mask = mine.value
elif hasattr(os, "sched_getaffinity"):
    mask = sum(1 << cpu for cpu in os.sched_getaffinity(0))
else:
    mask = 0
os.makedirs("calls", exist_ok=True)   # a file per call: concurrent appends to one file lose lines
with open(os.path.join("calls", "%s.%d.%d.txt" % (name, a.seed, os.getpid())), "w") as log:
    log.write("%s %d %.3f %.3f" % (name, a.seed, started, time.time()))
json.dump({"kpis": {"cost": cost, "cpu_mask": mask}, "text_feedback": spec.get("note", "")}, open(a.out, "w"))
'''


def _instances(tmp_path: Path, **specs: dict) -> list[str]:
    (tmp_path / "solver.py").write_text(SOLVER, encoding="utf-8")
    (tmp_path / "data").mkdir(exist_ok=True)
    for name, spec in specs.items():
        (tmp_path / "data" / f"{name}.json").write_text(json.dumps(spec), encoding="utf-8")
    return [f"data/{name}.json" for name in specs]


def _raw(instances: list[str], **stage) -> dict:
    return {
        "problem": {
            "description": "Tune the solver.",
            "parameters": {
                "small": {"type": "float", "low": -0.5, "high": 0.5, "default": 0.0},
                "big": {"type": "float", "low": -0.5, "high": 0.5, "default": 0.0},
            },
        },
        "evaluate": {
            "stages": [
                {"id": "static", "kind": "builtin-static"},
                {
                    "id": "full",
                    "kind": "command",
                    "command": f'"{sys.executable}" solver.py --instance {{instance}} --seed {{seed}} --out {{out}} {{params}}',
                    "instances": instances,
                    "timeout": 60,
                    **stage,
                },
            ],
            "score": {"objective": "cost", "direction": "minimize"},
        },
        "search": {"operators": {"param_lhs": 1.0}, "novelty": {"behavioural": "off"}},
    }


def _candidate(config, cid: str, *, seed: bool = False, **values) -> Candidate:
    space = config.problem.parameters
    block = space.render_block({**space.defaults(), **values})
    source = splice_block(
        config.problem.skeleton_source(), block, config.problem.block_start, config.problem.block_end
    )
    return Candidate(
        id=cid, generation=0 if seed else 1, block=block, source=source,
        operator=SEED_OPERATOR if seed else "param_lhs",
    )


def _calls(tmp_path: Path) -> list[tuple[str, int, float, float]]:
    lines = [path.read_text(encoding="utf-8") for path in (tmp_path / "calls").glob("*.txt")]
    return [(n, int(s), float(a), float(b)) for n, s, a, b in (line.split() for line in lines)]


# -- configuration ---------------------------------------------------------


def test_listing_instances_needs_a_place_in_the_command_to_put_them(tmp_path):
    raw = _raw(_instances(tmp_path, s1={"scale": 100}))
    raw["evaluate"]["stages"][1]["command"] = "solver --out {out} {params}"
    with pytest.raises(ConfigError, match=r"instances: 1 instance\(s\) are listed but the command has no \{instance\}"):
        build_config(raw, base_dir=tmp_path)


def test_the_instance_placeholder_needs_instances(tmp_path):
    raw = _raw([])
    with pytest.raises(ConfigError, match=r"uses \{instance\} but the stage lists no `instances`"):
        build_config(raw, base_dir=tmp_path)


@pytest.mark.parametrize("key, value", [("workers", 3), ("retries", 1), ("pin_cpus", [0])])
def test_what_only_makes_sense_per_instance_is_refused_without_instances(tmp_path, minimal_raw, key, value):
    minimal_raw["evaluate"]["stages"].append(
        {"id": "full", "kind": "command", "command": "evaluate {candidate} {out}", key: value}
    )
    with pytest.raises(ConfigError, match=rf"{key}: only applies to a stage that lists `instances`"):
        build_config(minimal_raw, base_dir=tmp_path)


def test_every_worker_needs_a_cpu_of_its_own(tmp_path):
    raw = _raw(_instances(tmp_path, s1={"scale": 100}), workers=3, pin_cpus=[0])
    with pytest.raises(ConfigError, match="3 workers cannot each have a CPU of their own out of 1"):
        build_config(raw, base_dir=tmp_path)


def test_a_config_pinned_for_a_bigger_machine_loads_here_and_refuses_to_run_here(tmp_path):
    # `pin_cpus` was checked against this machine when the config was *read*,
    # so a config written for the 8-CPU benchmark machine could not even be
    # loaded -- let alone inspected, extended or tested -- on a 4-CPU laptop.
    raw = _raw(_instances(tmp_path, s1={"scale": 100}), pin_cpus=[4096])
    config = build_config(raw, base_dir=tmp_path)
    assert config.evaluate.stages[1].pin_cpus == (4096,)
    with pytest.raises(ConfigError, match=r"pin_cpus: CPU 4096 does not exist on this machine"):
        Cascade(config, work_dir=tmp_path / "work").evaluate_generation([_candidate(config, "g000-c0001", seed=True)])
    assert not (tmp_path / "calls").exists(), "nothing ran unpinned in the meantime"


@pytest.mark.parametrize("cpus, fragment", [([1, 1], "lists a CPU twice"), ([-1], "must be >= 0")])
def test_the_shape_of_pin_cpus_is_still_checked_when_the_config_is_read(tmp_path, cpus, fragment):
    raw = _raw(_instances(tmp_path, s1={"scale": 100}), pin_cpus=cpus)
    with pytest.raises(ConfigError, match=fragment):
        build_config(raw, base_dir=tmp_path)


def test_a_pattern_means_the_files_that_are_there_in_a_stable_order(tmp_path):
    _instances(tmp_path, s2={"scale": 1}, s1={"scale": 1}, b1={"scale": 1})
    config = build_config(_raw(["data/s*.json", "data/b1.json"]), base_dir=tmp_path)
    stage = config.evaluate.stages[1]
    assert stage.instances == ("data/s1.json", "data/s2.json", "data/b1.json")
    assert stage.instance_names() == ("s1", "s2", "b1")


def test_a_pattern_that_matches_nothing_is_said_now_not_after_the_first_generation(tmp_path):
    _instances(tmp_path, s1={"scale": 1})
    with pytest.raises(ConfigError, match=r"instances\[0\]: the pattern 'data/\*.vrp' matches no file"):
        build_config(_raw(["data/*.vrp"]), base_dir=tmp_path)


def test_an_instance_does_not_have_to_be_a_file(tmp_path):
    config = build_config(_raw(["X-n101-k25", "X-n106-k14"]), base_dir=tmp_path)
    assert config.evaluate.stages[1].instance_names() == ("X-n101-k25", "X-n106-k14")


def test_the_instance_reaches_the_command_as_one_argument(tmp_path):
    argv = build_argv(
        "solver --instance {instance} --out {out}", candidate=tmp_path / "c.py", inputs=[],
        out=tmp_path / "o.json", instance="data/two words.vrp",
    )
    assert argv == ["solver", "--instance", "data/two words.vrp", "--out", str(tmp_path / "o.json")]


# -- one run per instance --------------------------------------------------


def test_every_instance_and_seed_is_a_run_of_its_own_and_instances_weigh_the_same(tmp_path):
    instances = _instances(tmp_path, s1={"scale": 100}, b1={"scale": 1000, "note": "slow to converge"})
    config = build_config(_raw(instances, seeds=2, normalize="none"), base_dir=tmp_path)
    result = Cascade(config, work_dir=tmp_path / "work").evaluate_generation(
        [_candidate(config, "g000-c0001", seed=True)]
    )["g000-c0001"]

    assert sorted(_calls(tmp_path))[0][:2] == ("b1", 0) and len(_calls(tmp_path)) == 4
    final = result.outcomes[-1]
    assert final.instance_names == ("s1", "b1")
    assert final.vector_kpis["cost_per_instance"] == [100.25, 1000.25], "seeds average within an instance"
    assert result.kpis["cost"] == pytest.approx(550.25) and final.runs == 4
    assert final.kpi_cv["cost"] > 0, "the spread between seeds, not between instances"
    assert final.kpi_cv["cost"] < 0.01
    assert final.text_feedback == "b1: slow to converge"
    assert result.competes is True


def test_every_run_is_reported_under_its_instances_name(tmp_path):
    instances = _instances(tmp_path, s1={"scale": 100}, b1={"scale": 1000})
    config = build_config(_raw(instances), base_dir=tmp_path)
    events: list[dict] = []
    cascade = Cascade(config, work_dir=tmp_path / "run" / "work", on_event=lambda t, **f: events.append({"type": t, **f}))
    cascade.evaluate_generation([_candidate(config, "g000-c0001", seed=True)])
    finished = [e for e in events if e["type"] == "eval_finished"]
    assert [(e["instance"], e["attempt"], e["ok"]) for e in finished] == [("s1", 0, True), ("b1", 0, True)]
    assert finished[0]["kpis"]["cost"] == 100.0, "the run's own value, not yet a percentage of anything"
    assert finished[0]["stdout_log"] == "work/stage_out/g000-c0001.full.s1.seed0.stdout.log"
    assert "data/s1.json" in finished[0]["argv"]



def test_instances_whose_names_differ_only_in_punctuation_keep_files_of_their_own(tmp_path):
    # `a b` and `a_b` were both written as `...full.a_b.seed0.*`: the second run
    # overwrote the first one's logs, and with two workers one instance could
    # read the other's `{out}` and report its result as its own.
    instances = _instances(tmp_path, **{"a b": {"scale": 100, "sleep": 0.5}, "a_b": {"scale": 1000}})
    config = build_config(_raw(instances, workers=2, normalize="none"), base_dir=tmp_path)
    events: list[dict] = []
    cascade = Cascade(config, work_dir=tmp_path / "run" / "work", on_event=lambda t, **f: events.append({"type": t, **f}))
    result = cascade.evaluate_generation([_candidate(config, "g000-c0001", seed=True)])["g000-c0001"]
    finished = {e["instance"]: e for e in events if e["type"] == "eval_finished"}
    assert finished["a b"]["stdout_log"] != finished["a_b"]["stdout_log"]
    assert finished["a b"]["kpis"]["cost"] == 100.0 and finished["a_b"]["kpis"]["cost"] == 1000.0
    assert result.outcomes[-1].vector_kpis["cost_per_instance"] == [100.0, 1000.0]


def test_file_names_are_unique_even_where_the_file_system_ignores_case():
    from evolvekit.evaluate.fanout import _file_names

    assert _file_names(["s1", "b1"]) == ("s1", "b1"), "names that do not collide are left as they are"
    assert len({name.lower() for name in _file_names(["A", "a", "a-1"])}) == 3
    assert len({name.lower() for name in _file_names(["x y", "x_y", "x.y"])}) == 3


# -- a failure with an address ---------------------------------------------


def test_a_crash_names_its_instance_and_stops_costing(tmp_path):
    instances = _instances(tmp_path, s1={"scale": 100}, s2={"scale": 100, "crash": True}, s3={"scale": 100}, s4={"scale": 100})
    config = build_config(_raw(instances), base_dir=tmp_path)
    result = Cascade(config, work_dir=tmp_path / "work").evaluate_generation(
        [_candidate(config, "g000-c0001", seed=True)]
    )["g000-c0001"]
    assert result.competes is False
    assert "exit code 3 (instance s2)" in result.last_failure and "cannot solve s2" in result.last_failure
    assert [name for name, *_ in _calls(tmp_path)] == ["s1"], "s3 and s4 were never started"


def test_a_candidate_that_fails_does_not_take_the_others_with_it(tmp_path):
    instances = _instances(tmp_path, s1={"scale": 100}, b1={"scale": 1000})
    config = build_config(_raw(instances), base_dir=tmp_path)
    good, bad = _candidate(config, "g001-c0002"), _candidate(config, "g001-c0003")
    # A configuration the solver refuses: not caught by validation, because the
    # flag is rendered by the space -- so break the candidate's copy of it.
    cascade = Cascade(config, work_dir=tmp_path / "work")
    real = cascade._configure

    def sabotage(candidate_id, path, params):
        real(candidate_id, path, params)
        if candidate_id == bad.id:
            configuration = cascade._configurations[candidate_id]
            cascade._configurations[candidate_id] = type(configuration)(
                flags=("--no-such-flag", "1"), json_path=configuration.json_path
            )

    cascade._configure = sabotage
    results = cascade.evaluate_generation([good, bad])
    assert results[good.id].competes is True and results[good.id].kpis["cost"] == 550.0
    assert results[bad.id].competes is False and "exit code 2 (instance s1)" in results[bad.id].last_failure


def test_a_run_that_crashed_once_is_run_again_and_its_logs_are_kept(tmp_path):
    instances = _instances(tmp_path, s1={"scale": 100, "flaky": True}, b1={"scale": 1000})
    config = build_config(_raw(instances, retries=1), base_dir=tmp_path)
    events: list[dict] = []
    cascade = Cascade(config, work_dir=tmp_path / "run" / "work", on_event=lambda t, **f: events.append({"type": t, **f}))
    result = cascade.evaluate_generation([_candidate(config, "g000-c0001", seed=True)])["g000-c0001"]
    assert result.competes is True and result.outcomes[-1].runs == 3
    attempts = [(e["instance"], e["attempt"], e["ok"]) for e in events if e["type"] == "eval_finished"]
    assert attempts == [("s1", 0, False), ("s1", 1, True), ("b1", 0, True)]
    out = tmp_path / "run" / "work" / "stage_out"
    assert "segfault" in (out / "g000-c0001.full.s1.seed0.stderr.log").read_text(encoding="utf-8")
    assert (out / "g000-c0001.full.s1.seed0.try1.json").is_file()


def test_without_retries_the_same_crash_fails_the_stage(tmp_path):
    instances = _instances(tmp_path, s1={"scale": 100, "flaky": True})
    config = build_config(_raw(instances), base_dir=tmp_path)
    result = Cascade(config, work_dir=tmp_path / "work").evaluate_generation(
        [_candidate(config, "g000-c0001", seed=True)]
    )["g000-c0001"]
    assert result.competes is False and "exit code 3 (instance s1)" in result.last_failure


# -- every instance has the same say ---------------------------------------


def test_the_baseline_scores_100_and_a_gain_counts_the_same_on_a_small_instance_as_on_a_big_one(tmp_path):
    instances = _instances(tmp_path, s1={"scale": 100}, b1={"scale": 10_000})
    config = build_config(_raw(instances), base_dir=tmp_path)
    cascade = Cascade(config, work_dir=tmp_path / "work")
    seed = cascade.evaluate_generation([_candidate(config, "g000-c0001", seed=True)])["g000-c0001"]
    assert seed.kpis["cost"] == pytest.approx(100.0) and seed.kpis["cost_raw"] == pytest.approx(5050.0)

    results = cascade.evaluate_generation([
        _candidate(config, "g001-c0002", small=-0.2),   # 20 % better on the small instance
        _candidate(config, "g001-c0003", big=-0.2),     # 20 % better on the big one
    ])
    small, big = results["g001-c0002"], results["g001-c0003"]
    assert small.kpis["cost"] == pytest.approx(90.0) and big.kpis["cost"] == pytest.approx(90.0)
    assert small.kpis["cost_raw"] == pytest.approx(5040.0), "the plain mean barely notices the small instance"
    assert big.kpis["cost_raw"] == pytest.approx(4050.0)
    assert small.score == pytest.approx(-90.0)
    assert small.outcomes[-1].vector_kpis["cost_pct_of_baseline"] == pytest.approx([80.0, 100.0])


def test_the_yardstick_survives_a_restart_because_the_seed_is_not_evaluated_again(tmp_path):
    instances = _instances(tmp_path, s1={"scale": 100}, b1={"scale": 10_000})
    config = build_config(_raw(instances), base_dir=tmp_path)
    Cascade(config, work_dir=tmp_path / "work").evaluate_generation([_candidate(config, "g000-c0001", seed=True)])
    resumed = Cascade(config, work_dir=tmp_path / "work")
    result = resumed.evaluate_generation([_candidate(config, "g001-c0002", small=-0.2)])["g001-c0002"]
    assert result.kpis["cost"] == pytest.approx(90.0)


def test_a_baseline_of_zero_cannot_be_a_yardstick_and_says_what_to_do(tmp_path):
    instances = _instances(tmp_path, s1={"scale": 0}, b1={"scale": 10})
    config = build_config(_raw(instances), base_dir=tmp_path)
    result = Cascade(config, work_dir=tmp_path / "work").evaluate_generation(
        [_candidate(config, "g000-c0001", seed=True)]
    )["g000-c0001"]
    assert result.competes is False
    assert "cost on instance s1 is 0" in result.last_failure and "normalize: none" in result.last_failure


def test_normalize_none_is_the_plain_mean(tmp_path):
    instances = _instances(tmp_path, s1={"scale": 100}, b1={"scale": 10_000})
    config = build_config(_raw(instances, normalize="none"), base_dir=tmp_path)
    result = Cascade(config, work_dir=tmp_path / "work").evaluate_generation(
        [_candidate(config, "g000-c0001", seed=True)]
    )["g000-c0001"]
    assert result.kpis["cost"] == pytest.approx(5050.0) and "cost_raw" not in result.kpis


def test_held_out_instances_are_scored_apart_and_against_their_own_baseline(tmp_path):
    instances = _instances(tmp_path, s1={"scale": 100}, b1={"scale": 1000}, s9={"scale": 300})
    config = build_config(_raw(instances[:2], private_instances=instances[2:]), base_dir=tmp_path)
    cascade = Cascade(config, work_dir=tmp_path / "work")
    seed = cascade.evaluate_generation([_candidate(config, "g000-c0001", seed=True)])["g000-c0001"]
    assert seed.private_score == pytest.approx(-100.0) and seed.competes is True
    child = cascade.evaluate_generation([_candidate(config, "g001-c0002", small=-0.1, big=0.1)])["g001-c0002"]
    assert child.public_score == pytest.approx(-100.0), "10 % better on s1, 10 % worse on b1"
    assert child.private_score == pytest.approx(-90.0), "the held-out instance is a small one"


# -- workers ---------------------------------------------------------------


@pytest.mark.slow
def test_workers_run_side_by_side_but_never_more_than_asked_for(tmp_path):
    specs = {f"s{i}": {"scale": 100, "sleep": 0.8} for i in range(6)}
    config = build_config(_raw(_instances(tmp_path, **specs), workers=3), base_dir=tmp_path)
    cascade = Cascade(config, work_dir=tmp_path / "work")
    started = time.perf_counter()
    results = cascade.evaluate_generation(
        [_candidate(config, "g000-c0001", seed=True), _candidate(config, "g001-c0002", small=0.1)]
    )
    elapsed = time.perf_counter() - started
    assert all(r.competes for r in results.values())

    calls = _calls(tmp_path)
    assert len(calls) == 12
    moments = sorted([(a, 1) for *_, a, _b in calls] + [(b, -1) for *_, _a, b in calls])
    in_flight = peak = 0
    for _moment, change in moments:
        in_flight += change
        peak = max(peak, in_flight)
    assert peak == 3, "three at a time: not two, and never four"
    assert elapsed < 12 * 0.8, "twelve runs of 0.8 s took less than they would in a row"
    # Instance by instance across candidates: the first three runs to start are
    # both candidates' s0 and one s1 -- not one candidate's s0, s1 and s2.
    order = [name for name, *_ in sorted(calls, key=lambda call: call[2])]
    assert sorted(order[:3]) == ["s0", "s0", "s1"] and sorted(order[-3:]) == ["s4", "s5", "s5"]


@pytest.mark.skipif(not can_pin() or (os.cpu_count() or 1) < 2, reason="needs CPU pinning and two CPUs")
def test_each_worker_is_pinned_to_a_cpu_of_its_own(tmp_path):
    specs = {f"s{i}": {"scale": 100, "sleep": 0.3} for i in range(4)}
    config = build_config(_raw(_instances(tmp_path, **specs), workers=2, pin_cpus=[0, 1]), base_dir=tmp_path)
    events: list[dict] = []
    cascade = Cascade(config, work_dir=tmp_path / "run" / "work", on_event=lambda t, **f: events.append({"type": t, **f}))
    cascade.evaluate_generation([_candidate(config, "g000-c0001", seed=True)])
    masks = [int(e["kpis"]["cpu_mask"]) for e in events if e["type"] == "eval_finished"]
    assert len(masks) == 4 and set(masks) == {1, 2}, "one logical CPU per run: CPU 0 or CPU 1, never both"


def test_a_run_can_be_called_off(tmp_path):
    cancel = threading.Event()
    threading.Timer(0.5, cancel.set).start()
    started = time.perf_counter()
    run = run_bounded(
        [sys.executable, "-c", "import time; time.sleep(60)"], timeout=60, cwd=tmp_path,
        stdout_path=tmp_path / "o.log", stderr_path=tmp_path / "e.log", cancel=cancel,
    )
    assert run.cancelled is True and run.returncode is None and not run.timed_out
    assert time.perf_counter() - started < 10


def test_the_daily_cap_is_applied_on_admission(tmp_path):
    from evolvekit.budget import BudgetGuard

    instances = _instances(tmp_path, s1={"scale": 100})
    raw = _raw(instances)
    raw["budget"] = {"max_full_evals_per_day": 2}
    config = build_config(raw, base_dir=tmp_path)
    cascade = Cascade(config, work_dir=tmp_path / "work", budget=BudgetGuard(config.budget))
    results = cascade.evaluate_generation(
        [_candidate(config, "g000-c0001", seed=True), _candidate(config, "g001-c0002", small=0.1),
         _candidate(config, "g001-c0003", small=0.2)]
    )
    assert [r.competes for r in results.values()] == [True, True, False]
    assert "daily full-evaluation cap reached" in results["g001-c0003"].last_failure
    assert len(_calls(tmp_path)) == 2


# -- what the status document makes of it ----------------------------------


def test_two_instances_of_one_candidate_in_flight_are_two_runs(tmp_path):
    from evolvekit.status import build_status
    from tests.test_status import NOW, RunDir

    run = RunDir(tmp_path)
    run.started(100)
    run.lock()
    run.heartbeat(1)
    common = {"candidate_id": "g001-c0002", "stage": "full", "seed": 0, "private": False, "attempt": 0}
    run.event("eval_started", 30, timeout_s=600, instance="t01", **common)
    run.event("eval_started", 30, timeout_s=600, instance="t02", **common)
    run.event("eval_finished", 5, ok=True, duration_s=25.0, kpis={"cost": 1.0}, instance="t01", **common)
    health = build_status(tmp_path, now=NOW)["health"]
    assert [(f["instance"], f["attempt"]) for f in health["in_flight"]] == [("t02", 0)]
    assert health["evaluations"]["done"] == 1


@pytest.mark.slow
def test_a_whole_run_reads_back_instance_by_instance(tmp_path):
    from evolvekit.search.driver import Driver
    from evolvekit.status import build_status, render_text

    instances = _instances(tmp_path, s1={"scale": 100}, s2={"scale": 300, "flaky": True}, b1={"scale": 10_000})
    raw = _raw(instances, workers=2, retries=1)
    raw["search"].update({"children_per_generation": 4, "generations": 2, "seed": 1})
    raw["budget"] = {"max_full_evals_per_day": 100}
    config = build_config(raw, base_dir=tmp_path)
    summary = Driver(config, run_dir=tmp_path / "run").run()
    assert summary.best.score > summary.seed_score == pytest.approx(-100.0)

    document = build_status(tmp_path / "run")
    assert document["errors"] == []
    stage = [s for s in document_stages(tmp_path / "run") if s["final"]][0]
    assert stage["instances"] == ["s1", "s2", "b1"] and stage["normalize"] == "baseline" and stage["workers"] == 2

    progress = document["progress"]
    assert progress["baseline"]["objective"] == pytest.approx(100.0)
    assert progress["baseline"]["n"] == 3 and progress["baseline"]["sd"] == 0.0, "no floating-point dust"
    assert document["objective"]["unit"] == "% of the baseline, per instance"
    assert progress["spread"] == {
        "across": "instances", "available": True,
        "why": "each instance's value in percent of the baseline's, as the objective counts it",
    }
    assert progress["improvement"]["pct"] > 0
    assert progress["improvement"]["verdict"] in ("clear", "within noise")
    assert "shared run(s) on 3 instance(s)" in progress["improvement"]["why"]

    per_instance = document["instances"]
    assert per_instance["available"] is True and [r["name"] for r in per_instance["rows"]] == ["s1", "s2", "b1"]
    assert per_instance["rows"][2]["baseline"] == pytest.approx(10_000.0), "raw values, instance by instance"

    failures = document["failures"]
    assert [(f["instance"], f["attempt"], f["recovered"]) for f in failures] == [("s2", 0, True)]
    assert "instance s2" in render_text(document) and "(a retry went through)" in render_text(document)


def document_stages(run_dir: Path) -> list[dict]:
    from evolvekit.events import read_events

    return read_events(run_dir)[0]["stages"]


# -- preflight -------------------------------------------------------------


def test_preflight_runs_every_instance_and_projects_the_wall_clock_of_the_pool(tmp_path):
    from evolvekit.preflight import preflight

    specs = {f"s{i}": {"scale": 100, "sleep": 0.4} for i in range(4)}
    config = build_config(_raw(_instances(tmp_path, **specs), workers=2), base_dir=tmp_path)
    report = preflight(config)
    assert report.failures == []
    full = report.stages[-1]
    assert full.runs == 4 and full.workers == 2 and full.kpis["cost"] == pytest.approx(100.0)
    assert full.wall_s == pytest.approx(full.duration_s / 2)
    assert report.full_eval_s == pytest.approx(full.wall_s), "what a generation pays per candidate, on the clock"
    assert any("no `pin_cpus`" in note for note in report.notes)


def test_preflight_warns_when_there_are_more_workers_than_physical_cores(tmp_path, monkeypatch):
    from evolvekit import preflight as module

    monkeypatch.setattr(module.os, "cpu_count", lambda: 4)
    config = build_config(_raw(_instances(tmp_path, s1={"scale": 100}), workers=3), base_dir=tmp_path)
    report = module.preflight(config)
    assert any("3 workers on a machine with 4 logical CPUs" in w for w in report.warnings)


def test_a_long_stage_says_how_far_it_is_and_how_long_the_rest_will_take(tmp_path):
    """Two candidates x three instances on two workers; four runs of 600 s are
    done, one candidate has crashed for good, one run has been going for 100 s."""
    from evolvekit.status import build_status, render_text
    from tests.test_status import NOW, RunDir

    run = RunDir(tmp_path)
    run.started(5000, planned=3)
    run.lock()
    run.heartbeat(1)
    run.event("generation_started", 5000, generation=0)
    run.event("generation_finished", 4000, generation=0, duration_s=1000.0)
    run.event("generation_started", 3900, generation=1)
    run.event("generation_finished", 2900, generation=1, duration_s=1000.0)
    run.event("generation_started", 1400, generation=2)
    run.event("stage_started", 1400, stage="full", private=False, workers=2,
              candidates=["g002-c0005", "g002-c0006"], runs_per_candidate=3)
    base = {"stage": "full", "seed": 0, "private": False, "attempt": 0}
    for cid, instance, ago in [("g002-c0005", "t01", 800), ("g002-c0006", "t01", 800), ("g002-c0005", "t02", 200)]:
        run.event("eval_started", ago + 600, timeout_s=700, candidate_id=cid, instance=instance, **base)
        run.event("eval_finished", ago, ok=True, duration_s=600.0, kpis={"cost": 1.0},
                  candidate_id=cid, instance=instance, **base)
    run.event("eval_started", 210, timeout_s=700, candidate_id="g002-c0006", instance="t02", **base)
    run.event("eval_finished", 200, ok=False, duration_s=10.0, failure="exit code 139",
              candidate_id="g002-c0006", instance="t02", **base)
    run.event("eval_started", 100, timeout_s=700, candidate_id="g002-c0005", instance="t03", **base)

    health = build_status(tmp_path, now=NOW)["health"]
    stage = health["stage"]
    assert (stage["id"], stage["runs_planned"], stage["runs_done"]) == ("full", 6, 3)
    assert stage["candidates_out"] == 1 and stage["runs_left"] == 1, "c0006 crashed: its t03 will never start"
    assert stage["typical_run_s"] == 600.0 and stage["workers"] == 2
    assert stage["remaining_s"] == pytest.approx((600.0 - 100.0) / 2)
    # The generation in progress has used 1400 s of a typical 1000 s, so what is
    # left of it is what its stage has left; then one more generation.
    assert health["eta"]["seconds"] == pytest.approx(250.0 + 1000.0)
    assert "stage      : full -- 3 of 6 run(s) done for 2 candidates, 2 at a time, about 4 min left" in render_text(
        build_status(tmp_path, now=NOW)
    )
