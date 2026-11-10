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
