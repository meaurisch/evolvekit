"""A study from start to finish on the demo harness: the preview, the test
run, the search and the final check (`evolvekit/harness/execute.py`)."""

from __future__ import annotations

import json
import shutil
import sqlite3
import threading
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from evolvekit.confirm import paired_summary
from evolvekit.harness.execute import (
    Job, _extend_check, final_check, preview, read_job, read_preview, resume_study, run_study, run_test,
)
from evolvekit.harness.library import create_study
from evolvekit.harness.plan import CHECK_SEEDS, Plan
from evolvekit.harness.study import Guardrail, load_study, save_study
from evolvekit.ledger import read_jsonl
from evolvekit.stopping import STOP_REQUEST

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "harnesses" / "demo-tour"
SOLVER = ROOT / "examples" / "cli-solver" / "solver.py"


def _study(tmp_path: Path, template: str = "tune", hours: float = 0.004) -> Path:
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
        summary = paired_summary([-9.0, 11.5, -0.4] if len(seeds) < settled_at else [1.3, 0.6, 1.9])
        return SimpleNamespace(per_candidate={"c1": {"summary": summary}})
    return fake_confirm


def test_the_check_adds_runs_while_the_answer_is_open_and_time_remains(tmp_path):
    calls: list[int] = []
    clock = iter([400.0, 300.0, 200.0, 100.0, 10.0, 10.0])
    job = Job(tmp_path, run_id="r", study="s", plan={})
    comparison, seeds = _extend_check(_three_held_back(tmp_path), _plan_with(), None, tmp_path, job, lambda _m: None,
                                      stop=threading.Event(), time_left_s=lambda: next(clock),
                                      confirm=_confirm_recording(calls))
    assert calls == [3, 4, 5, 6], "open at 3, 4 and 5 runs per case; settled at 6"
    assert seeds == 6 and job.state["check"] == {"seeds": 6}
    assert read_job(tmp_path)["check"] == {"seeds": 6}
    assert comparison.per_candidate["c1"]["summary"]["n"] == 3


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
    # Four workers make a round cheaper: ceil(6 / 4) * 20 = 40 s, so 100 s pays for one more, 30 s for none.
    clock = iter([100.0, 30.0])
    assert run(_plan_with(workers=4), time_left=lambda: next(clock))[0] == [3, 4]
