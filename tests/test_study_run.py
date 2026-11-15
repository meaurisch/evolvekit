"""A study from start to finish on the demo harness: the preview, the test
run, the search and the final check (`evolvekit/harness/execute.py`)."""

from __future__ import annotations

import json
import math
import random
import shutil
import sqlite3
import threading
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from evolvekit.confirm import BASELINE, Comparison, _analyse, verdict_summary
from evolvekit.harness.execute import (
    Job, _extend_check, continue_study, final_check, preview, read_job, read_preview, resume_study, run_study, run_test,
)
from evolvekit.harness.library import create_study
from evolvekit.harness.plan import CHECK_SEEDS, Plan
from evolvekit.harness.study import Guardrail, load_study, save_study
from evolvekit.ledger import read_jsonl
from evolvekit.stopping import STOP_REQUEST

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "harnesses" / "demo-tour"
SOLVER = ROOT / "examples" / "cli-solver" / "solver.py"


def _study(tmp_path: Path, template: str = "tune", hours: float = 0.01) -> Path:
    folder = tmp_path / "study"
    create_study(folder, DEMO, "Demo settings", template)
    for sample in sorted((DEMO / "samples").glob("*.json")):
        shutil.copy(sample, folder / "cases" / sample.name)
    study = load_study(folder)
    study.application_path = str(SOLVER)
    study.training = ["cases/north-south-30.json", "cases/town-40.json", "cases/county-50.json"]
    study.test = ["cases/region-60.json"]
    study.limits.time_per_case_s = 0.5
    study.budget.hours = hours
    save_study(study, folder)
    return folder


def _fix_plan(folder: Path, generations: int = 3) -> None:
    # The automatic plan is drawn from this machine's measured speed: on a slow
    # runner 14 s buys fewer than 3 rounds and the plan refuses to start. The
    # tests that use this are about the search and the check, not the plan,
    # so the plan is fixed.
    study = load_study(folder)
    study.plan = {"auto": False, "children": 4, "generations": generations}
    save_study(study, folder)


def test_a_new_study_pins_its_harness_without_the_samples(tmp_path):
    folder = tmp_path / "s"
    study = create_study(folder, DEMO, "My study", "tune")
    assert (folder / "harness" / "harness.yaml").is_file() and (folder / "harness" / "runner.py").is_file()
    assert not (folder / "harness" / "samples").exists()
    assert (folder / "cases").is_dir() and (folder / "inputs").is_dir()
    assert load_study(folder) == study and study.tuned_settings() == ["neighbours", "restart_after", "accept_worse", "init"]


def test_the_preview_measures_every_kpi_on_the_smallest_case_and_keeps_the_tables(tmp_path):
    folder = _study(tmp_path)
    result = preview(folder)
    assert result["ok"], result
    assert result["case"] == "cases/north-south-30.json"
    assert set(result["kpis"]) >= {"tour_length", "solver_length", "longest_leg", "iterations", "solve_s"}
    assert result["overhead_s"] >= 0 and result["wall_s"] >= 0.5
    assert read_preview(folder) == result
    db = sqlite3.connect(folder / "preview" / "tables.sqlite")
    assert db.execute("SELECT COUNT(*) FROM stops").fetchone() == (30,)
    assert db.execute("SELECT COUNT(*) FROM orig_stops").fetchone() == (30,)


def test_a_preview_that_cannot_start_the_application_says_why(tmp_path):
    folder = _study(tmp_path)
    study = load_study(folder)
    study.application_path = str(tmp_path / "nowhere" / "solver.py")
    save_study(study, folder)
    result = preview(folder)
    assert not result["ok"] and result["exit_code"] == 1
    assert result["error"].startswith("the solver failed with exit code 2"), result["error"]


def test_the_test_run_checks_the_compiled_study_on_one_case(tmp_path):
    folder = _study(tmp_path)
    result = run_test(folder)
    assert result["ok"], result["failures"]
    assert result["case"] == "cases/north-south-30.json"
    assert result["kpis"]["tour_length"] > 0 and result["guardrails_hold"]


def test_the_test_run_says_when_the_starting_point_breaks_a_guardrail(tmp_path):
    folder = _study(tmp_path)
    study = load_study(folder)
    study.guardrails = [Guardrail(kpi="tour_length", max=1.0)]
    save_study(study, folder)
    result = run_test(folder)
    assert not result["ok"]
    assert any("the seed breaks a gate: guardrail_violation" in failure for failure in result["failures"])


@pytest.mark.slow
def test_a_study_runs_its_search_and_then_the_final_check(tmp_path):
    folder = _study(tmp_path)
    _fix_plan(folder)
    job = run_study(folder, "r1", log=lambda _m: None)
    run_dir = folder / "runs" / "r1"
    assert job["phase"] == "done", job
    assert read_job(run_dir) == job
    assert (run_dir / "evolvekit.yaml").is_file() and (run_dir / "study-run.json").is_file()
    assert job["search"]["stop_reason"].startswith(("time limit", "generations exhausted")), job["search"]
    final = job["final"]
    if "skipped" in final:
        assert final["skipped"].startswith("the starting point stayed the best")
    else:
        comparison = json.loads((run_dir / final["comparison"]).read_text(encoding="utf-8"))
        assert comparison["instances"] == ["region-60"] and comparison["seeds"][0] == 1001
        assert final["candidate"] in comparison["per_candidate"]
        assert final["decision"] in ("use", "keep", "worse", "single")
        assert isinstance(final.get("seeds"), int) and len(comparison["seeds"]) == final["seeds"]


@pytest.mark.slow
def test_a_study_run_writes_rounds_left_after_every_round(tmp_path, monkeypatch):
    """A4.1: the run re-plans after every round from what the rounds measured,
    and `job.json`'s plan says it: the rounds done, the rounds the time left
    buys, the measured round. The planned numbers stay as they were planned."""
    folder = _study(tmp_path)
    _fix_plan(folder)
    written: list[object] = []
    write = Job._write

    def recording(self):
        written.append((self.state.get("plan") or {}).get("rounds_done"))
        write(self)

    monkeypatch.setattr(Job, "_write", recording)
    job = run_study(folder, "r1", log=lambda _m: None)
    plan = job["plan"]
    assert job["search"]["generations"] >= 1, job["search"]
    assert plan["rounds_done"] == job["search"]["generations"]
    assert isinstance(plan["rounds_left"], int) and plan["rounds_left"] >= 0
    assert plan["measured"]["round_s"] > 0 and 0 <= plan["measured"]["ai_child_s"] < 1.0, "no AI: milliseconds to write"
    assert plan["rounds"] == plan["generations"] == 3, "the plan as it was set"
    done = [n for n in written if isinstance(n, int)]
    assert sorted(set(done)) == list(range(plan["rounds_done"] + 1)), "rewritten after every round, the last included"
    assert done == sorted(done), "never backwards"


_ROUND_TOY = r'''
import argparse, json
p = argparse.ArgumentParser()
p.add_argument("--seed", type=int, default=0); p.add_argument("--x", type=float, default=0.0)
a = p.parse_args()
print(json.dumps({"value": a.x + a.seed / 1000.0}))
'''


def test_the_driver_calls_on_round_after_each_recorded_round_and_stops_on_a_reason(tmp_path):
    from evolvekit.config import build_config
    from evolvekit.events import read_events
    from evolvekit.search.driver import Driver

    (tmp_path / "toy.py").write_text(_ROUND_TOY, encoding="utf-8")
    config = build_config({
        "problem": {"parameters": {"x": {"type": "float", "low": 0.0, "high": 1.0, "default": 0.0}}},
        "evaluate": {
            "stages": [{"id": "static", "kind": "builtin-static"},
                       {"id": "full", "kind": "command", "kpis_from": "stdout",
                        "command": "{python} toy.py --seed {seed} {params}"}],
            "score": {"objective": "value", "direction": "maximize"},
        },
        "search": {"operators": {"param_lhs": 1.0}},
    }, base_dir=tmp_path)
    calls: list[int] = []

    def on_round(generation: int) -> str | None:
        calls.append(generation)
        return "converged: test" if len(calls) == 3 else None

    run_dir = tmp_path / "run"
    summary = Driver(config, run_dir=run_dir, on_round=on_round).run(generations=6)
    assert calls == [1, 2, 3], "never for the starting point, once for every round"
    assert summary.generations == 3 and summary.stop_reason == "converged: test" and summary.converged
    assert not Driver(config, run_dir=tmp_path / "plain").run(generations=1).converged
    finished = [e for e in read_events(run_dir) if e["type"] == "generation_finished"]
    assert [e["generation"] for e in finished] == [0, 1, 2, 3]
    for event in finished:
        assert {"round_best", "breed_s", "adopted", "noise_sd", "tpe_ratio", "breadth_at_ceiling"} <= set(event)
        assert event["breed_s"] >= 0 and event["tpe_ratio"] is None and event["breadth_at_ceiling"] is None
        assert event["adopted"] is False
        assert event["round_best"] == event["best_fitness"], "no re-evaluation here"
    assert finished[0]["breed_s"] == 0.0, "the starting point is not bred"


def test_the_round_event_says_whether_adaptive_breadth_ended_at_its_ceiling(tmp_path):
    """`breadth_at_ceiling`: after the round's own breadth update. A flat toy never
    improves, so with `grow_after: 1` the breadth grows 2 -> 3 (its max) in round 1."""
    from evolvekit.config import build_config
    from evolvekit.events import read_events
    from evolvekit.search.driver import Driver

    (tmp_path / "toy.py").write_text(_ROUND_TOY.replace("a.x + a.seed / 1000.0", "1.0"), encoding="utf-8")
    config = build_config({
        "problem": {"parameters": {"x": {"type": "float", "low": 0.0, "high": 1.0, "default": 0.0}}},
        "evaluate": {
            "stages": [{"id": "static", "kind": "builtin-static"},
                       {"id": "full", "kind": "command", "kpis_from": "stdout",
                        "command": "{python} toy.py --seed {seed} {params}"}],
            "score": {"objective": "value", "direction": "maximize"},
        },
        "search": {"operators": {"param_lhs": 1.0}, "children_per_generation": 2,
                   "adaptive_children": {"min": 2, "max": 3, "grow_after": 1}},
    }, base_dir=tmp_path)
    Driver(config, run_dir=tmp_path / "run").run(generations=2)
    finished = [e for e in read_events(tmp_path / "run") if e["type"] == "generation_finished"]
    assert [(e["generation"], e["breadth_at_ceiling"]) for e in finished] == [(0, False), (1, True), (2, True)]


def test_the_round_event_records_the_best_tpe_score_and_the_big_steps_tried(tmp_path):
    """`tpe_ratio`: the largest log density ratio among the round's TPE proposals (for
    engineers; convergence does not read it, decision 4), None in a round without one --
    TPE needs MIN_OBSERVATIONS configurations first. `big_steps`: the round's children
    planned as big steps (0 here: no model)."""
    from evolvekit.config import build_config
    from evolvekit.events import read_events
    from evolvekit.ledger import read_jsonl
    from evolvekit.search.driver import Driver

    (tmp_path / "toy.py").write_text(_ROUND_TOY, encoding="utf-8")
    config = build_config({
        "problem": {"parameters": {"x": {"type": "float", "low": 0.0, "high": 1.0, "default": 0.0}}},
        "evaluate": {
            "stages": [{"id": "static", "kind": "builtin-static"},
                       {"id": "full", "kind": "command", "kpis_from": "stdout",
                        "command": "{python} toy.py --seed {seed} {params}"}],
            "score": {"objective": "value", "direction": "maximize"},
        },
        "search": {"operators": {"param_tpe": 1.0}, "children_per_generation": 4},
    }, base_dir=tmp_path)
    Driver(config, run_dir=tmp_path / "run").run(generations=4)
    finished = {e["generation"]: e for e in read_events(tmp_path / "run") if e["type"] == "generation_finished"}
    tpe = {int(r["generation"]) for r in read_jsonl(tmp_path / "run" / "runs.jsonl") if r.get("operator") == "param_tpe"}
    assert tpe, "TPE proposed in some round"
    for generation, event in finished.items():
        assert event["big_steps"] == 0
        if generation in tpe:
            assert isinstance(event["tpe_ratio"], float), generation
        else:
            assert event["tpe_ratio"] is None, generation


@pytest.mark.slow
def test_a_rerun_final_check_uses_the_cases_the_run_held_back(tmp_path):
    folder = _study(tmp_path)
    _fix_plan(folder)
    job = run_study(folder, "r1", log=lambda _m: None)
    assert (folder / "runs" / "r1" / "study.yaml").is_file()
    if "skipped" in job["final"]:
        # A slow machine may find nothing better than the starting point in
        # 14 s; then there is no check to run again, here or on CI.
        pytest.skip("the search found nothing better, so there is no final check to run again")
    study = load_study(folder)
    study.training, study.test = study.training[1:] + study.test, [study.training[0]]   # swap after the run
    save_study(study, folder)
    job = final_check(folder, "r1", seeds=2, log=lambda _m: None)
    comparison = json.loads((folder / "runs" / "r1" / job["final"]["comparison"]).read_text(encoding="utf-8"))
    assert comparison["instances"] == ["region-60"], "the run's own held-back case, not today's"


@pytest.mark.slow
def test_a_stop_request_during_the_search_ends_the_job_as_stopped(tmp_path):
    folder = _study(tmp_path, hours=0.05)
    run_dir = folder / "runs" / "r2"

    def request_stop() -> None:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            job = read_job(run_dir)
            if job and job.get("phase") == "search" and (run_dir / "events.jsonl").is_file():
                time.sleep(1.5)
                (run_dir / STOP_REQUEST).write_text("stop", encoding="utf-8")
                return
            time.sleep(0.1)

    stopper = threading.Thread(target=request_stop)
    stopper.start()
    began = time.monotonic()
    job = run_study(folder, "r2", log=lambda _m: None)
    stopper.join()
    assert job["phase"] == "stopped", job
    assert job["search"]["stop_reason"] == "stopped on request"
    assert time.monotonic() - began < 40, "the study was given three minutes; the stop ended it"


def test_a_stop_before_the_run_began_ends_it_without_a_search(tmp_path):
    folder = _study(tmp_path)
    run_dir = folder / "runs" / "r3"; run_dir.mkdir(parents=True)
    (run_dir / STOP_REQUEST).write_text("stop", encoding="utf-8")
    began = time.monotonic()
    job = run_study(folder, "r3", log=lambda _m: None)
    assert job["phase"] == "stopped" and job["search"]["stop_reason"] == "stopped on request before the search began"
    assert not (run_dir / "runs.jsonl").exists() and time.monotonic() - began < 5


@pytest.mark.slow
def test_a_stopped_search_is_resumed_to_the_end_of_its_plan(tmp_path):
    folder = _study(tmp_path, hours=0.05)
    _fix_plan(folder, generations=4)
    run_dir = folder / "runs" / "r4"

    def request_stop() -> None:
        # Once a round after the starting point is on record, so the stop lands
        # in the middle of the next one: that is what a resume picks up from.
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            rows = list(read_jsonl(run_dir / "runs.jsonl"))
            if any(int(r.get("generation") or 0) >= 1 for r in rows):
                (run_dir / STOP_REQUEST).write_text("stop", encoding="utf-8")
                return
            time.sleep(0.1)

    stopper = threading.Thread(target=request_stop)
    stopper.start()
    job = run_study(folder, "r4", log=lambda _m: None)
    stopper.join()
    assert job["phase"] == "stopped" and job["search"]["stop_reason"] == "stopped on request", job
    assert 1 <= job["search"]["generations"] < 4, job["search"]
    resumed = resume_study(folder, "r4", log=lambda _m: None)
    assert resumed["phase"] == "done", resumed
    assert resumed["search"]["generations"] == 4, "the plan's rounds in all, not the rounds of the second session"
    assert resumed["resumed_at"] and resumed["final"]
    assert resumed["started_at"] == job["started_at"], "the time budget counts from the first start"
    assert read_job(run_dir) == resumed


@pytest.mark.slow
def test_a_finished_study_goes_on_with_more_time(tmp_path):
    folder = _study(tmp_path)
    _fix_plan(folder)
    run_dir = folder / "runs" / "r5"
    job = run_study(folder, "r5", log=lambda _m: None)
    assert job["phase"] == "done", job
    before = job["search"]["generations"]
    more = continue_study(folder, "r5", hours=0.004, log=lambda _m: None)
    assert more["phase"] == "done", more
    assert more["search"]["generations"] > before, "the rounds of the first session and the new ones, in all"
    assert more["final"] and more["continued_at"] and more["hours_added"] == 0.004
    assert more["started_at"] == job["started_at"], "the same run, with more time: not a second one"
    assert read_job(run_dir) == more
    # A continuation whose process died: job.json still says `search`, with the plan's search on record.
    (run_dir / "job.json").write_text(json.dumps({**more, "phase": "search"}), encoding="utf-8")
    again = continue_study(folder, "r5", hours=0.004, log=lambda _m: None)
    assert again["phase"] == "done" and again["search"]["generations"] > more["search"]["generations"], again
    assert again["started_at"] == job["started_at"], "still the same run"
    with pytest.raises(Exception, match="not finished"):
        continue_study(folder, "nowhere", hours=0.004, log=lambda _m: None)


# -- the check adds runs on its own ----------------------------------------


def _three_held_back(tmp_path: Path):
    study = create_study(tmp_path / "s", DEMO, "Three held back", "tune")
    study.test = ["cases/a.json", "cases/b.json", "cases/c.json"]
    return study


def _plan_with(**changes):
    return replace(Plan.fixed(), **{"check_seeds": 3, "workers": 1, "run_s": 20.0, "test_cases": 3, **changes})


def _confirm_recording(calls: list[int], settled_at: int = 6):
    """A `confirm` that costs nothing: open (`keep`) below `settled_at` runs per
    case, settled (`use`) from there on."""
    def fake_confirm(config, run_dir, *, seeds, instances, label, log, stop):
        assert list(seeds) == list(CHECK_SEEDS[:len(seeds)]), "every call names the whole seed list, so runs are kept"
        assert len(instances) == 3 and label == "final"
        calls.append(len(seeds))
        summary = verdict_summary([-0.09, 0.115, -0.004] if len(seeds) < settled_at else [0.013, 0.006, 0.019],
                                  direction="minimize")
        return SimpleNamespace(per_candidate={"c1": {"summary": summary}})
    return fake_confirm


def test_the_check_says_how_often_the_answer_was_read(tmp_path):
    lines: list[str] = []
    calls: list[int] = []
    fake = _confirm_recording(calls)

    def counting(*args, **kwargs):
        comparison = fake(*args, **kwargs)
        comparison.looks = len(calls)
        return comparison

    job = Job(tmp_path, run_id="r", study="s", plan={})
    _extend_check(_three_held_back(tmp_path), _plan_with(), None, tmp_path, job, lines.append,
                  stop=threading.Event(), time_left_s=lambda: 1e9, confirm=counting)
    assert lines[-2:] == ["final check: added 9 runs per held-back case and read the answer again",
                          "final check: the answer was read 2 times"]


def test_the_check_adds_every_run_it_can_at_once_and_reads_the_answer_once_more(tmp_path):
    """Decision 12: no peeking. Look 1 after the planned runs; while open, every run the cap
    and the time allow is added at once, and look 2 is the answer, whatever it says."""
    calls: list[int] = []
    study = _three_held_back(tmp_path)
    job = Job(tmp_path, run_id="r", study="s", plan={})
    # A run per case costs ceil(2 * 3 / 1) * 20 s = 120 s: 400 s pays for three more.
    comparison, seeds = _extend_check(study, _plan_with(), None, tmp_path, job, lambda _m: None,
                                      stop=threading.Event(), time_left_s=lambda: 400.0,
                                      confirm=_confirm_recording(calls))
    assert calls == [3, 6], "open at 3 runs per case; all three more added, then read once"
    assert seeds == 6 and job.state["check"] == {"seeds": 6}
    assert read_job(tmp_path)["check"] == {"seeds": 6}
    assert comparison.per_candidate["c1"]["summary"]["n"] == 3
    calls.clear()
    _, seeds = _extend_check(study, _plan_with(), None, tmp_path, job, lambda _m: None,
                             stop=threading.Event(), time_left_s=lambda: 1e9, confirm=_confirm_recording(calls, 99))
    assert calls == [3, 12] and seeds == 12, "still open at look 2: it stops anyway"
    calls.clear()
    _extend_check(study, _plan_with(), None, tmp_path, job, lambda _m: None,
                  stop=threading.Event(), time_left_s=lambda: 1e9, confirm=_confirm_recording(calls, 3))
    assert calls == [3], "settled at look 1: nothing is added"


def test_the_check_stops_adding_runs_when_time_the_seeds_or_a_stop_say_so(tmp_path):
    study, log = _three_held_back(tmp_path), (lambda _m: None)

    def run(plan, *, time_left, stop: threading.Event | None = None):
        calls: list[int] = []
        job = Job(tmp_path, run_id="r", study="s", plan={})
        _, seeds = _extend_check(study, plan, None, tmp_path, job, log, stop=stop or threading.Event(),
                                 time_left_s=time_left, confirm=_confirm_recording(calls, settled_at=99))
        return calls, seeds, job.state["check"]["seeds"]

    # One more run per case costs ceil(2 * 3 / 1) * 20 s = 120 s: 30 s left does not pay for it.
    assert run(_plan_with(), time_left=lambda: 30.0) == ([3], 3, 3)
    # Never past the 12 seeds there are, however much time there is.
    assert run(_plan_with(check_seeds=12), time_left=lambda: 1e9) == ([12], 12, 12)
    assert run(_plan_with(check_seeds=11), time_left=lambda: 1e9) == ([11, 12], 12, 12)
    # A stop request keeps the comparison it has.
    stopped = threading.Event()
    stopped.set()
    assert run(_plan_with(), time_left=lambda: 1e9, stop=stopped) == ([3], 3, 3)
    # Four workers make a round cheaper: ceil(6 / 4) * 20 = 40 s, so 100 s pays for two more, 30 s for none.
    assert run(_plan_with(workers=4), time_left=lambda: 100.0)[0] == [3, 5]
    assert run(_plan_with(workers=4), time_left=lambda: 30.0)[0] == [3]


def test_a_check_of_an_explicit_number_of_runs_is_never_extended(tmp_path):
    calls: list[int] = []
    job = Job(tmp_path, run_id="r", study="s", plan={})
    free = _plan_with(run_s=0.0)  # a run that costs nothing would otherwise always fit
    _, seeds = _extend_check(_three_held_back(tmp_path), free, None, tmp_path, job, lambda _m: None,
                             stop=threading.Event(), time_left_s=lambda: 0.0,
                             confirm=_confirm_recording(calls, settled_at=99), extend=False)
    assert calls == [3] and seeds == 3


def _confirm_of_cases(calls: list[int], *, case_gains: list[float], run_sd: float):
    """A `confirm` that runs nothing: the real comparison (`confirm._analyse`) of held-back
    cases whose true log gains are `case_gains`, each run noisy by `run_sd`."""
    def fake_confirm(config, run_dir, *, seeds, instances, label, log, stop):
        calls.append(len(seeds))
        comparison = Comparison(label="final", objective="cost", direction="minimize", stage="full",
                                seeds=list(seeds), instances=list(instances), baseline_id="seed", candidates=["c1"])
        for instance, gain in zip(instances, case_gains):
            for seed in seeds:
                rng = random.Random(f"{instance}/{seed}")
                comparison.runs += [
                    {"configuration": BASELINE, "instance": instance, "seed": seed, "ok": True,
                     "kpis": {"cost": 100.0 * math.exp(rng.gauss(0.0, run_sd))}},
                    {"configuration": "c1", "instance": instance, "seed": seed, "ok": True,
                     "kpis": {"cost": 100.0 * math.exp(-gain + rng.gauss(0.0, run_sd))}},
                ]
        _analyse(comparison)
        return comparison
    return fake_confirm


def test_the_check_stops_adding_runs_when_the_cases_set_the_answer(tmp_path):
    """A toy whose cases differ by far more than its runs: more runs cannot open the answer."""
    calls: list[int] = []
    lines: list[str] = []
    job = Job(tmp_path, run_id="r", study="s", plan={})
    _, seeds = _extend_check(_three_held_back(tmp_path), _plan_with(), None, tmp_path, job, lines.append,
                             stop=threading.Event(), time_left_s=lambda: 1e9,
                             confirm=_confirm_of_cases(calls, case_gains=[0.10, -0.08, 0.01], run_sd=0.001))
    assert calls == [3] and seeds == 3
    assert job.state["check"] == {"seeds": 3, "limited_by": "cases"}
    assert "final check: the answer is set by the held-back cases, not by the runs: hold back more cases "            "for a firmer answer" in lines


def test_the_check_adds_runs_when_the_runs_are_what_is_unsure(tmp_path):
    calls: list[int] = []
    job = Job(tmp_path, run_id="r", study="s", plan={})
    _extend_check(_three_held_back(tmp_path), _plan_with(), None, tmp_path, job, lambda _m: None,
                  stop=threading.Event(), time_left_s=lambda: 1e9,
                  confirm=_confirm_of_cases(calls, case_gains=[0.004, 0.0, 0.002], run_sd=0.05))
    assert calls == [3, 12] and "limited_by" not in job.state["check"]


# -- a settled search ends early; its time is the check's allowance (A4.1, P1, P2) --

def _flat_events(path: Path, rounds: int, *, session: str = "s") -> None:
    """`events.jsonl` of a search whose every round measured the same best (noise sd 0.1):
    round g ends at 100 s * g, so the active seconds after round g are 100 * g."""
    rows = [{"type": "generation_finished", "session": session, "ts": f"2026-11-14T10:{g * 100 // 60:02d}:"
             f"{g * 100 % 60:02d}+00:00", "generation": g, "duration_s": 100.0, "breed_s": 0.0 if g == 0 else 10.0,
             "adopted": False, "children": 1 if g == 0 else 4, "round_best": 1.0, "best_fitness": 1.0,
             "noise_sd": 0.1, "big_steps": 0} for g in range(rounds + 1)]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _auto_plan(**changes) -> Plan:
    return replace(Plan.fixed(children=4, generations=8), search_s=3600.0, **changes)  # 6 rounds planned


def test_a_settled_search_ends_and_says_so_in_job_json(tmp_path):
    from evolvekit.harness.execute import _round_hook

    _flat_events(tmp_path / "events.jsonl", 3)
    job = Job(tmp_path, run_id="r", study="s", plan={"rounds": 6, "search_s": 3600.0})
    hook = _round_hook(tmp_path, job, _auto_plan(), log=lambda _m: None)
    assert hook(3) == "converged: no round gained beyond the noise for 3 rounds"
    assert read_job(tmp_path)["search"] == {"converged": True, "flat_rounds": 3, "window": 3, "used_s": 300.0}
    assert read_job(tmp_path)["plan"]["rounds_done"] == 3, "the re-plan is written too"
    _flat_events(tmp_path / "events.jsonl", 2)
    (tmp_path / "b").mkdir()
    fresh = Job(tmp_path / "b", run_id="r", study="s", plan={})
    assert _round_hook(tmp_path, fresh, _auto_plan(), log=lambda _m: None)(2) is None, "two flat rounds of three"
    assert "search" not in fresh.state


def test_hand_set_rounds_never_end_early(tmp_path):
    from evolvekit.harness.execute import _round_hook

    _flat_events(tmp_path / "events.jsonl", 5)
    for adjusted in (("rounds",), ("generations",)):
        job = Job(tmp_path, run_id="r", study="s", plan={})
        assert _round_hook(tmp_path, job, _auto_plan(adjusted=adjusted), log=lambda _m: None)(5) is None
        assert job.state["plan"]["rounds_left"] is not None and "search" not in job.state, adjusted
    job = Job(tmp_path, run_id="r", study="s", plan={})
    assert _round_hook(tmp_path, job, _auto_plan(adjusted=("children",)), log=lambda _m: None)(5), \
        "children set by hand: the rounds are still the plan's"


def test_a_search_that_takes_big_steps_waits_for_them(tmp_path):
    from evolvekit.harness.execute import _round_hook

    _flat_events(tmp_path / "events.jsonl", 4)
    job = Job(tmp_path, run_id="r", study="s", plan={})
    assert _round_hook(tmp_path, job, _auto_plan(), min_big_steps=1, log=lambda _m: None)(4) is None


def test_convergence_is_rebuilt_from_events_on_resume(tmp_path):
    from evolvekit.harness.execute import _round_hook, _stored_plan

    _flat_events(tmp_path / "events.jsonl", 3)
    first = Job(tmp_path, run_id="r", study="s", plan=_auto_plan().to_json())
    said = _round_hook(tmp_path, first, _auto_plan(), log=lambda _m: None)(3)
    config = SimpleNamespace(budget=SimpleNamespace(max_hours=1.0), search=SimpleNamespace(children_per_generation=4,
                                                                                          generations=8))
    resumed = _stored_plan(first.state["plan"], config)
    assert (resumed.rounds, resumed.adjusted) == (6, ())
    for name in ("again", "hand"):
        (tmp_path / name).mkdir()
    again = Job(tmp_path / "again", run_id="r", study="s", plan=first.state["plan"])
    assert _round_hook(tmp_path, again, resumed, log=lambda _m: None)(3) == said
    assert again.state["search"] == first.state["search"]
    assert again.state["plan"]["rounds_left"] == first.state["plan"]["rounds_left"]
    hand = _stored_plan({**first.state["plan"], "adjusted": ["rounds"]}, config)
    assert hand.adjusted == ("rounds",)
    assert _round_hook(tmp_path, Job(tmp_path / "hand", run_id="r", study="s", plan={}), hand,
                       log=lambda _m: None)(3) is None, "a hand-set plan stays hand-set when resumed"


def test_run_study_keeps_what_the_hook_said_of_a_settled_search(tmp_path, monkeypatch):
    from evolvekit.harness import execute

    folder = _study(tmp_path)
    monkeypatch.setattr(execute, "require_valid", lambda *args, **kwargs: None)
    monkeypatch.setattr(execute, "measured", lambda _root: None)
    monkeypatch.setattr(execute, "plan_for", lambda *args, **kwargs: _auto_plan())
    monkeypatch.setattr(execute, "write_run", lambda study, harness, plan, root, run_id, **kw:
                        (root / "runs" / run_id).mkdir(parents=True, exist_ok=True))
    monkeypatch.setattr(execute, "load_config", lambda path: SimpleNamespace(
        stop=SimpleNamespace(min_big_steps=0), search=SimpleNamespace(takes_big_steps=False)))
    checked: list[dict] = []
    monkeypatch.setattr(execute, "_final_check", lambda study, plan, config, run_dir, job, log, **kw:
                        (checked.append(dict(job.state["search"])), job.finish("done")))

    class FakeDriver:
        def __init__(self, config, *, run_dir, on_round=None, **kwargs):
            self.run_dir, self.on_round = Path(run_dir), on_round

        def run(self):
            for g in range(1, 7):
                _flat_events(self.run_dir / "events.jsonl", g)
                reason = self.on_round(g)
                if reason:
                    return SimpleNamespace(stop_reason=reason, generations=g, best=None, aborted=False, converged=True)
            raise AssertionError("a flat search settles")

    monkeypatch.setattr("evolvekit.search.driver.Driver", FakeDriver)
    job = execute.run_study(folder, "r1", log=lambda _m: None)
    assert checked == [job["search"]]
    assert job["search"] == {"stop_reason": "converged: no round gained beyond the noise for 3 rounds",
                             "generations": 3, "best_id": None, "aborted": False, "converged": True,
                             "flat_rounds": 3, "window": 3, "used_s": 300.0}


def _settled_check(tmp_path, monkeypatch, *, confirm, time_left: float, search: dict | None = None,
                   check_seeds: int = 3):
    """`_final_check` of a run whose search settled after 600 s of a planned 1800 s (1200 s
    saved), through the real `_extend_check` with `confirm`; `time_left` is what `_time_left`
    gives (from the run's start, the saved time included)."""
    from evolvekit.harness import execute

    tmp_path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(execute, "_extend_check", lambda *a, **kw: _extend_check(*a, **kw, confirm=confirm))
    monkeypatch.setattr(execute, "_time_left", lambda job, plan: (lambda: time_left))
    monkeypatch.setattr(execute, "rank", lambda rows, n: [{"operator": "param_tpe"}])
    monkeypatch.setattr(execute, "read_rows", lambda run_dir: [])
    monkeypatch.setattr(execute, "_write_exports", lambda *args: None)
    job = Job(tmp_path, run_id="r", study="s", plan={"search_s": 1800.0})
    job.update(search=search if search is not None else
               {"stop_reason": "converged: …", "generations": 3, "converged": True, "used_s": 600.0})
    execute._final_check(_three_held_back(tmp_path), _plan_with(check_seeds=check_seeds), None, tmp_path, job,
                         lambda _m: None)
    return job.state


def test_a_settled_search_gives_the_check_its_saved_time(tmp_path, monkeypatch):
    """A run per case costs ceil(2 * 3 / 1) * 20 s = 120 s. 400 s left pays for three more;
    the settled run's 400 s + the saved 1200 s pays for all nine to the 12-run cap, and the
    answer is still read twice, never a third time."""
    calls: list[int] = []
    plain = _settled_check(tmp_path / "a", monkeypatch, confirm=_confirm_recording(calls, 99), time_left=400.0,
                           search={"stop_reason": "the plan's rounds are done", "generations": 6})
    assert calls == [3, 6] and "saved" not in plain["final"], "a search that did not settle saved nothing"
    calls.clear()
    settled = _settled_check(tmp_path / "b", monkeypatch, confirm=_confirm_recording(calls, 99), time_left=1600.0)
    assert calls == [3, 12], "never more than 12 runs a case, never a third read"
    assert settled["final"]["saved"] == {"s": 1200, "spent": True, "why": "extended"}
    assert settled["final"]["seeds"] == 12


def test_the_saved_time_is_not_spent_when_the_cases_set_the_answer(tmp_path, monkeypatch):
    calls: list[int] = []
    state = _settled_check(tmp_path, monkeypatch, time_left=1600.0,
                           confirm=_confirm_of_cases(calls, case_gains=[0.10, -0.08, 0.01], run_sd=0.001))
    assert calls == [3] and state["check"]["limited_by"] == "cases"
    assert state["final"]["saved"] == {"s": 1200, "spent": False, "why": "cases"}


def test_the_saved_time_is_not_spent_when_the_first_read_answered_or_no_run_could_be_added(tmp_path, monkeypatch):
    calls: list[int] = []
    answered = _settled_check(tmp_path / "a", monkeypatch, confirm=_confirm_recording(calls, 3), time_left=1600.0)
    assert calls == [3] and answered["final"]["saved"] == {"s": 1200, "spent": False, "why": "answered"}
    calls.clear()
    full = _settled_check(tmp_path / "b", monkeypatch, confirm=_confirm_recording(calls, 99), time_left=1600.0,
                          check_seeds=12)
    assert calls == [12] and full["final"]["saved"] == {"s": 1200, "spent": False, "why": "at_cap"}
    calls.clear()
    overspent = _settled_check(tmp_path / "c", monkeypatch, confirm=_confirm_recording(calls, 3), time_left=1600.0,
                               search={"stop_reason": "converged: …", "generations": 3, "converged": True,
                                       "used_s": 2000.0})
    assert overspent["final"]["saved"]["s"] == 0, "never below none"


def test_a_resumed_settled_search_with_no_round_left_keeps_what_it_said_and_its_saved_time(tmp_path, monkeypatch):
    """M3: the session that settled wrote `search.converged` and died in its final check; the
    resumed session's search has no round left (the driver runs none, so the hook says
    nothing). The run keeps `converged` and its check still records `final.saved`."""
    from evolvekit.harness import execute

    folder = _study(tmp_path)
    study = load_study(folder)
    study.test = ["cases/region-60.json", "cases/town-40.json", "cases/county-50.json"]
    study.training = ["cases/north-south-30.json"]
    save_study(study, folder)
    run_dir = folder / "runs" / "r1"
    run_dir.mkdir(parents=True)
    (run_dir / "evolvekit.yaml").write_text("{}\n", encoding="utf-8")
    (run_dir / "runs.jsonl").write_text(json.dumps({"id": "g003-c0001", "generation": 3}) + "\n", encoding="utf-8")
    _flat_events(run_dir / "events.jsonl", 3)
    plan = {**_plan_with().to_json(), "search_s": 1800.0}
    settled = {"stop_reason": "converged: no round gained beyond the noise for 3 rounds", "generations": 3,
               "best_id": None, "aborted": False, "converged": True, "flat_rounds": 3, "window": 3, "used_s": 600.0}
    (run_dir / "job.json").write_text(json.dumps({"phase": "check", "run_id": "r1", "study": "s", "plan": plan,
                                                  "started_at": "2026-11-14T10:00:00+00:00", "search": settled}),
                                      encoding="utf-8")
    monkeypatch.setattr(execute, "load_config", lambda path: None)

    class NoRoundLeft:
        def __init__(self, config, *, run_dir, on_round=None, **kwargs):
            pass

        def run(self):
            return SimpleNamespace(stop_reason="generations exhausted", generations=0, best=None, aborted=False,
                                   converged=False)

    monkeypatch.setattr("evolvekit.search.driver.Driver", NoRoundLeft)
    calls: list[int] = []
    monkeypatch.setattr(execute, "_extend_check",
                        lambda *a, **kw: _extend_check(*a, **kw, confirm=_confirm_recording(calls, 3)))
    monkeypatch.setattr(execute, "rank", lambda rows, n: [{"operator": "param_tpe"}])
    monkeypatch.setattr(execute, "read_rows", lambda run_dir: [])
    monkeypatch.setattr(execute, "_write_exports", lambda *args: None)
    job = resume_study(folder, "r1", log=lambda _m: None)
    assert job["phase"] == "done" and calls == [3]
    assert job["search"]["stop_reason"] == settled["stop_reason"], "the settled reason, so how it ran says so"
    assert {k: job["search"][k] for k in ("converged", "flat_rounds", "window", "used_s")} == {
        "converged": True, "flat_rounds": 3, "window": 3, "used_s": 600.0}
    assert job["final"]["saved"] == {"s": 1200, "spent": False, "why": "answered"}
    # A resumed session that ran rounds and did not settle keeps none of it.
    from evolvekit.harness.execute import Job as RunJob, _searched

    fresh = RunJob(tmp_path, run_id="r", study="s", plan={})
    fresh.update(search=settled)
    ran = SimpleNamespace(stop_reason="time limit reached", generations=2, best=None, aborted=False, converged=False)
    assert "converged" not in _searched(fresh, ran, 5)


def _settled_null_rate(setting: str, studies: int) -> tuple[float, float, int]:
    """The share of `studies` seeded null studies (`random.Random(20261114)`) that the real
    `execute._extend_check` answers "use": 4 held-back cases x 3 runs, each case's true log
    gain N(0, case sd), each run noisy by the run sd, and the time allowance of a settled
    search -- enough for all 12 runs per case. Its `confirm` is the real `confirm._analyse`
    of `test_confirm._a_study`'s synthetic runs. Returns the rate, its SE and the most reads
    any study took."""
    from test_confirm import _a_study

    from evolvekit.confirm import decision_of

    case_sd, run_sd = {"runs noisier than cases": (0.005, 0.02), "pure run noise": (0.0, 0.002)}[setting]
    rng = random.Random(20261114)
    study = SimpleNamespace(test=[f"cases/c{i}.json" for i in range(4)])
    plan = SimpleNamespace(check_seeds=3, workers=1, run_s=1.0)
    job = SimpleNamespace(update=lambda **fields: None)
    seed_cost = math.ceil(2 * 4 / 1) * 1.0
    uses = most = 0
    for _ in range(studies):
        look = _a_study(rng, gains=[rng.gauss(0.0, case_sd) for _ in range(4)], run_sd=run_sd)
        reads: list[int] = []

        def confirm(config, run_dir, *, seeds, instances, label, log, stop):
            reads.append(len(seeds))
            return SimpleNamespace(per_candidate={"c": look(len(seeds))}, looks=len(reads))

        comparison, _seeds = _extend_check(study, plan, None, None, job, lambda _m: None, stop=threading.Event(),
                                           time_left_s=lambda: 9 * seed_cost + 0.5, confirm=confirm)
        uses += decision_of(comparison.per_candidate["c"]) == "use"
        most = max(most, len(reads))
    rate = uses / studies
    return rate, math.sqrt(rate * (1 - rate) / studies), most


@pytest.mark.parametrize("setting", ["runs noisier than cases", "pure run noise"])
def test_a_settled_null_study_says_use_in_under_10_percent_through_the_real_extension(setting, capsys):
    """Handoff item 3: the product's procedure, not a copy -- execute._extend_check itself,
    with the allowance of a settled search (all 12 runs per case). Estimates (builder check,
    40 000 studies): runs noisier than cases (case sd 0.005, run sd 0.02) 8.78 % +- 0.14; pure
    run noise (every case 0, run sd 0.002) 8.83 % +- 0.14. 6000 seeded studies each, measured
    8.28 % +- 0.36 and 9.10 % +- 0.37: 10 % sits 4.8 and 2.4 SE above (decision 7: at 4000 the
    pure-noise rate was 2.07 SE below, too near). Asserts < 10 %, prints the rate. The recheck
    is not part of this path."""
    rate, se, most = _settled_null_rate(setting, 6000)
    with capsys.disabled():
        print(f"\n{setting}: settled null 'use' {rate:.2%} +- {se:.2%} (6000 studies)")
    assert most <= 2, "never a third read"
    assert rate < 0.10


@pytest.mark.slow
@pytest.mark.parametrize("setting", ["runs noisier than cases", "pure run noise"])
def test_a_settled_null_study_says_use_in_under_10_percent_through_the_real_extension_two_standard_errors_clear(
        setting, capsys):
    """The same on 10 000 studies per setting: rate + 2 SE < 10 % (estimate 8.8 % +- 0.29)."""
    rate, se, most = _settled_null_rate(setting, 10_000)
    with capsys.disabled():
        print(f"\n{setting}: settled null 'use' {rate:.2%} +- {se:.2%}, rate + 2 SE {rate + 2 * se:.2%} "
              "(10 000 studies)")
    assert most <= 2
    assert rate + 2 * se < 0.10


# -- the line against the harness defaults: information about the same runs (A4.3c, decision 6)


def _warm_check(tmp_path, monkeypatch, *, up=None):
    """`_final_check` of a warm-started study in a library home whose check says use (the real
    `_extend_check` over `_confirm_recording`); returns the job's state and the run's folder."""
    import yaml

    from evolvekit.app.store import Home
    from evolvekit.harness import execute

    home = Home(tmp_path / "home")
    slug = home.create_study("demo-tour", "tune", "Autumn")
    root = home.study_root(slug)
    data = yaml.safe_load((root / "study.yaml").read_text(encoding="utf-8"))
    data["warm"] = {"from": "summer", "run": "r1", "at": "2026-01-01T00:00:00+00:00"}
    (root / "study.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    study = load_study(root)
    study.test = ["cases/a.json", "cases/b.json", "cases/c.json"]
    run_dir = root / "runs" / "r1"
    run_dir.mkdir(parents=True)
    monkeypatch.setattr(execute, "_extend_check",
                        lambda *a, **kw: _extend_check(*a, **kw, confirm=_confirm_recording([], 3)))
    monkeypatch.setattr(execute, "rank", lambda rows, n: [{"operator": "param_tpe"}])
    monkeypatch.setattr(execute, "read_rows", lambda run_dir: [])
    monkeypatch.setattr(execute, "_write_exports", lambda *args: None)
    job = Job(run_dir, run_id="r1", study=slug, plan={})
    job.update(search={"stop_reason": "the plan's rounds are done", "generations": 3})
    lines: list[str] = []
    kw = {} if up is None else {"up": up}
    execute._final_check(study, _plan_with(), None, run_dir, job, lines.append, **kw)
    return job.state, run_dir, lines


def test_the_line_against_the_defaults_can_never_cost_the_answer(tmp_path, monkeypatch):
    """Decision 15a: a failure finding the defaults study, or writing the line, is logged and the
    final check finishes with its own answer; a run per kind of case gets no line."""
    from evolvekit.harness import execute, memory
    from evolvekit.harness.compile import GROUP_UP

    def broken(*args, **kwargs):
        raise RuntimeError("a damaged study folder")

    monkeypatch.setattr(memory, "defaults_study", broken)
    state, run_dir, lines = _warm_check(tmp_path / "a", monkeypatch)
    assert state["phase"] == "done" and state["final"]["decision"] == "use" and state["final"]["confirmed"] is True
    saved = json.loads((run_dir / "confirm" / "final" / "against_defaults.json").read_text(encoding="utf-8"))
    assert saved == {"measured": False, "why": "the other studies could not be read"}, "a fixed reason for people"
    assert any("RuntimeError: a damaged study folder" in line for line in lines), "the exception is in the log"
    monkeypatch.setattr(memory, "defaults_study", lambda *a, **k: None)
    real = execute._atomic_write

    def no_line(path, text):
        if Path(path).name == "against_defaults.json":
            raise OSError("disk full")
        real(path, text)

    monkeypatch.setattr(execute, "_atomic_write", no_line)
    state, run_dir, lines = _warm_check(tmp_path / "b", monkeypatch)
    assert state["phase"] == "done" and state["final"]["decision"] == "use"
    assert not (run_dir / "confirm" / "final" / "against_defaults.json").exists()
    assert any("not written: OSError: disk full" in line for line in lines)
    monkeypatch.setattr(execute, "_atomic_write", real)
    state, run_dir, lines = _warm_check(tmp_path / "c", monkeypatch, up=GROUP_UP)
    assert state["phase"] == "done" and not (run_dir / "confirm" / "final" / "against_defaults.json").exists(), \
        "one group's folder of a run per kind of case: no line"
    assert not any("against the harness defaults" in line for line in lines)


_NULL_CASES = 4
_NULL_NAMES = [f"c{i}" for i in range(_NULL_CASES)]


def _null_runs(config: str, value, seeds) -> list[dict]:
    """One run per held-back case and seed of `config`, its cost `value(case, seed)`."""
    return [{"configuration": config, "instance": name, "seed": s, "ok": True, "kpis": {"cost": value(i, s)}}
            for i, name in enumerate(_NULL_NAMES) for s in seeds]


def _null_analysed(cand, base, seeds) -> Comparison:
    """`cand` against `base` on the held-back cases and `seeds`, by the real `confirm._analyse`."""
    comparison = Comparison(label="final", objective="cost", direction="minimize", stage="full", seeds=list(seeds),
                            instances=list(_NULL_NAMES), baseline_id="b", candidates=["c"],
                            runs=_null_runs(BASELINE, base, seeds) + _null_runs("c", cand, seeds))
    _analyse(comparison)
    return comparison


def _rates(counts: dict) -> dict[str, tuple[float, float, int]]:
    return {key: (hits / max(1, n), math.sqrt((hits / max(1, n)) * (1 - hits / max(1, n)) / max(1, n)), n)
            for key, (hits, n) in counts.items()}


def _chain_line_null_rates(studies: int, case_sd: float = 0.005,
                           run_sd: float = 0.02) -> dict[str, tuple[float, float, int]]:
    """Decision 15c, the usual path: the defaults study is the warm study's own source. On
    `studies` seeded null pairs (`random.Random(20261115)`, case sd 0.005, run sd 0.02, 4 held-back
    cases, every configuration alike on average): a defaults study checks its winner W against
    the defaults D through the real `execute._extend_check`; only when it says use (the warm start
    takes W) the warm study, on the same cases, checks a new candidate C against W -- W's runs at
    a seed are the very runs the defaults study measured (a run repeats at its seed) -- through
    `_extend_check` again, then the line: `execute.defaults_comparison` of the side that verdict
    names against the defaults study's own results (its D runs, every seed it ran), read once --
    what the line would say -- and `execute.defaults_line`, what the product writes there.
    Decision 17, beside it: a LATER study S3 from the defaults on the same cases, its own runs of
    the defaults with the same case effects and fresh run noise (`random.Random(20261116)`, so the
    first path's draws are unchanged), read against the same side -- the study the line would be
    read against, not itself in the chain; the product writes "not measured" there too, as the
    chain's root held back those cases (`chose`).
    Returns {"defaults use", "line", "line after use", "line after no use", "measured", "S3 line",
    "S3 measured"} (rate, SE, count); the line's counts are of the pairs whose defaults study said
    use."""
    from evolvekit.confirm import decision_of
    from evolvekit.harness.execute import defaults_comparison, defaults_line

    rng, rng3 = random.Random(20261115), random.Random(20261116)
    study = SimpleNamespace(test=_NULL_NAMES)
    plan = SimpleNamespace(check_seeds=3, workers=1, run_s=1.0)
    job = SimpleNamespace(update=lambda **fields: None)
    counts = {"defaults use": [0, 0], "line": [0, 0], "line after use": [0, 0], "line after no use": [0, 0],
              "measured": [0, 0], "S3 line": [0, 0], "S3 measured": [0, 0]}

    def value(effect, noise):
        return lambda i, s: 100 * math.exp(effect[i] + noise[i][s])

    def check(cand, base):
        def confirm(config, run_dir, *, seeds, instances, label, log, stop):
            return _null_analysed(cand, base, range(len(seeds)))
        comparison, _seeds = _extend_check(study, plan, None, None, job, lambda _m: None, stop=threading.Event(),
                                           time_left_s=lambda: 1e9, confirm=confirm)
        return comparison, decision_of(comparison.per_candidate["c"])

    for _ in range(studies):
        cd, cw, cc = ([rng.gauss(0, case_sd) for _ in range(_NULL_CASES)] for _ in range(3))
        ed, ew, ec = ([[rng.gauss(0, run_sd) for _ in range(12)] for _ in range(_NULL_CASES)] for _ in range(3))
        defaults, winner, new = value(cd, ed), value(cw, ew), value(cc, ec)
        first, said = check(winner, defaults)
        counts["defaults use"][0] += said == "use"
        counts["defaults use"][1] += 1
        if said != "use":
            continue
        warm, decision = check(new, winner)
        against = defaults_comparison(warm, decision, first.runs, key_of_ours=str, key_of_theirs=str)
        (result,) = against.per_candidate.values()
        line = decision_of(result) == "use"
        for key in ("line", "line after use" if decision == "use" else "line after no use"):
            counts[key][0] += line
            counts[key][1] += 1
        # What the product writes on this path: not measured, never the replayed number.
        counts["measured"][0] += defaults_line(against, decision, name="Summer", reuses=True)["measured"]
        counts["measured"][1] += 1
        later = value(cd, [[rng3.gauss(0, run_sd) for _ in range(12)] for _ in range(_NULL_CASES)])
        against3 = defaults_comparison(warm, decision, _null_runs(BASELINE, later, range(3)),
                                       key_of_ours=str, key_of_theirs=str)
        (result3,) = against3.per_candidate.values()
        counts["S3 line"][0] += decision_of(result3) == "use"
        counts["S3 line"][1] += 1
        counts["S3 measured"][0] += defaults_line(against3, decision, name="Winter", reuses=False,
                                                  chose="Summer")["measured"]
        counts["S3 measured"][1] += 1
    return _rates(counts)


def _defaults_line_null_rates(studies: int) -> dict[str, tuple[float, float, int]]:
    """The selected path as the product takes it, on `studies` seeded null studies
    (`random.Random(20261114)`, runs noisier than cases: case sd 0.005, run sd 0.02, 4 held-back
    cases): the main answer through the real `execute._extend_check` (3 runs per case, extended
    to 12 while it is open), then the line against the defaults through the product's pairing
    (`execute.defaults_comparison`, the side the verdict names: the new settings on use, else the
    starting point) against a defaults study's 3 runs per case, read once by `confirm._analyse`
    and `decision_of`. Every configuration has its own case effects and run noise, none better
    on average. Returns {"main", "line", "line after use", "line after no use"}: (rate of "use",
    SE, count it is a share of)."""
    from evolvekit.confirm import decision_of
    from evolvekit.harness.execute import defaults_comparison

    cases, case_sd, run_sd, names, runs, analysed = _NULL_CASES, 0.005, 0.02, _NULL_NAMES, _null_runs, _null_analysed

    def draw(rng):
        return [rng.gauss(0, case_sd) for _ in range(cases)]

    rng = random.Random(20261114)
    study = SimpleNamespace(test=names)
    plan = SimpleNamespace(check_seeds=3, workers=1, run_s=1.0)
    job = SimpleNamespace(update=lambda **fields: None)
    counts = {"main": [0, 0], "line": [0, 0], "line after use": [0, 0], "line after no use": [0, 0]}
    for _ in range(studies):
        cb, cc, cd = draw(rng), draw(rng), draw(rng)
        eb, ec, ed = ([[rng.gauss(0, run_sd) for _ in range(12)] for _ in range(cases)] for _ in range(3))

        def value(effect, noise):
            return lambda i, s: 100 * math.exp(effect[i] + noise[i][s])

        start, best, defaults = value(cb, eb), value(cc, ec), value(cd, ed)

        def confirm(config, run_dir, *, seeds, instances, label, log, stop):
            return analysed(best, start, range(len(seeds)))

        comparison, _seeds = _extend_check(study, plan, None, None, job, lambda _m: None, stop=threading.Event(),
                                           time_left_s=lambda: 1e9, confirm=confirm)
        decision = decision_of(comparison.per_candidate["c"])
        against = defaults_comparison(comparison, decision, runs(BASELINE, defaults, range(3)),
                                      key_of_ours=str, key_of_theirs=str)
        (result,) = against.per_candidate.values()
        line = decision_of(result) == "use"
        for key, counted in (("main", True), ("line", True), ("line after use", decision == "use"),
                             ("line after no use", decision != "use")):
            if counted:
                counts[key][0] += line if key != "main" else decision == "use"
                counts[key][1] += 1
    return {key: (hits / max(1, n), math.sqrt((hits / max(1, n)) * (1 - hits / max(1, n)) / max(1, n)), n)
            for key, (hits, n) in counts.items()}


def _say_rates(rates: dict, capsys) -> None:
    with capsys.disabled():
        print("\nnull studies, 'use':", "; ".join(f"{key} {rate:.2%} +- {se:.2%} (n={n})"
                                                   for key, (rate, se, n) in rates.items()))


def test_the_line_against_the_defaults_says_use_for_no_difference_in_about_6_percent_and_15_after_a_use(capsys):
    """Decision 6: the line is read after the main answer, on the same runs, so it is selected on.
    Estimates (builder check, 10 000 studies, the same seeds): main use 8.6 %; the line says use in
    6.2 % +- 0.24 overall, 14.9 % +- 1.2 after a main use, 5.4 % +- 0.24 otherwise. 3000 studies,
    measured 5.40 % +- 0.41 overall, 9.43 % +- 1.87 after a use (244 of them), 5.04 % +- 0.42
    otherwise: asserts the line overall in [4, 8] %, after a use in [3, 26] %, otherwise in
    [3.5, 7.5] % (every bound at least 3.4 SE from the measured rate; the slow test pins them)."""
    rates = _defaults_line_null_rates(3000)
    _say_rates(rates, capsys)
    assert 0.04 <= rates["line"][0] <= 0.08
    assert 0.03 <= rates["line after use"][0] <= 0.26
    assert 0.035 <= rates["line after no use"][0] <= 0.075


@pytest.mark.slow
def test_the_line_against_the_defaults_says_use_for_no_difference_in_about_6_percent_and_15_after_a_use_on_10000(
        capsys):
    """The same on 10 000 studies, the builder check's own draws: measured main use 8.60 % +- 0.28,
    the line 6.21 % +- 0.24 overall, 14.88 % +- 1.21 after a use (860), 5.39 % +- 0.24 otherwise.
    Asserts the line overall in [5.5, 7] %, after a use in [11, 19] %, otherwise in [4.6, 6.2] %
    (every bound at least 2.9 SE from the measured rate)."""
    rates = _defaults_line_null_rates(10_000)
    _say_rates(rates, capsys)
    assert 0.055 <= rates["line"][0] <= 0.07
    assert 0.11 <= rates["line after use"][0] <= 0.19
    assert 0.046 <= rates["line after no use"][0] <= 0.062


def test_in_the_warm_studys_own_chain_the_line_would_say_use_in_about_92_percent_so_it_is_not_measured(capsys):
    """Decision 15c (revised): the defaults study chose the warm study's starting point on the same
    cases and runs, so a line would replay that choice; the product writes "not measured" on every
    such pair (`execute.defaults_line`). What the line would say, measured, 3000 pairs: the defaults study said use in
    8.97 % +- 0.52; then the line says use in 91.45 % +- 1.70 (269), 92.34 % +- 1.65 after the warm
    study's keep (261). 20 000 pairs: 91.88 % +- 0.66 (1711). Asserts the line in [80, 98] % and
    after a keep in [85, 98] % (every bound at least 3.4 SE from the measured rate)."""
    rates = _chain_line_null_rates(3000)
    _say_rates(rates, capsys)
    assert rates["measured"][0] == 0 and rates["measured"][2] == rates["line"][2] > 0, "never a number there"
    assert 0.80 <= rates["line"][0] <= 0.98
    assert 0.85 <= rates["line after no use"][0] <= 0.98


def test_a_later_defaults_study_on_the_chains_cases_would_replay_the_choice_so_it_is_not_measured(capsys):
    """Decision 17: S3, a later study from the defaults on the cases the chain's root chose on,
    carries that root's case effects, so a line against it would replay the choice; the product
    writes "not measured" on every such pair. What it would say (runs repeat at their seed), 3000
    pairs: runs noisier than cases 16.36 % +- 2.26 (269); cases noisier than runs (case sd 0.02,
    run sd 0.005) 86.01 % +- 2.90 (143). (The statistician's check with fresh runs for the warm
    study's starting point: 9.9 % and 76.5 %.) Asserts [8, 26] % and [74, 97] % (every bound at
    least 3.7 SE from the measured rate)."""
    rates = _chain_line_null_rates(3000)
    cases = _chain_line_null_rates(3000, case_sd=0.02, run_sd=0.005)
    _say_rates(rates, capsys)
    _say_rates(cases, capsys)
    for got in (rates, cases):
        assert got["S3 measured"][0] == 0 and got["S3 measured"][2] == got["S3 line"][2] > 0, "never a number there"
    assert 0.08 <= rates["S3 line"][0] <= 0.26
    assert 0.74 <= cases["S3 line"][0] <= 0.97


@pytest.mark.slow
def test_in_the_warm_studys_own_chain_the_line_would_say_use_in_about_92_percent_so_it_is_not_measured_on_20000(
        capsys):
    """The same on 20 000 pairs: measured the defaults study's use 8.55 % +- 0.20; the line 91.88 %
    +- 0.66 (1711 pairs), 93.17 % +- 0.62 after a keep (1654), 54.39 % +- 6.60 after a use (57).
    Asserts the line in [89, 94.5] %, after a keep in [90.5, 95.5] %, after a use in [25, 85] %
    (every bound at least 3.8 SE from the measured rate)."""
    rates = _chain_line_null_rates(20_000)
    _say_rates(rates, capsys)
    assert rates["measured"][0] == 0 and rates["measured"][2] == rates["line"][2] > 0, "never a number there"
    assert 0.89 <= rates["line"][0] <= 0.945
    assert 0.905 <= rates["line after no use"][0] <= 0.955
    assert 0.25 <= rates["line after use"][0] <= 0.85


@pytest.mark.slow
def test_a_later_defaults_study_on_the_chains_cases_would_replay_the_choice_so_it_is_not_measured_on_20000(capsys):
    """The same on 20 000 pairs: runs noisier than cases 17.24 % +- 0.91 (1711); cases noisier than
    runs 86.12 % +- 1.10 (994). Asserts [14, 20.5] % and [82, 90] % (every bound at least 3.5 SE from
    the measured rate), and "not measured" on every pair."""
    rates = _chain_line_null_rates(20_000)
    cases = _chain_line_null_rates(20_000, case_sd=0.02, run_sd=0.005)
    _say_rates(rates, capsys)
    _say_rates(cases, capsys)
    for got in (rates, cases):
        assert got["S3 measured"][0] == 0 and got["S3 measured"][2] == got["S3 line"][2] > 0, "never a number there"
    assert 0.14 <= rates["S3 line"][0] <= 0.205
    assert 0.82 <= cases["S3 line"][0] <= 0.90
