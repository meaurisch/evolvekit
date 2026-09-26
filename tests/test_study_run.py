"""A study from start to finish on the demo harness: the preview, the test
run, the search and the final check (`evolvekit/harness/execute.py`)."""

from __future__ import annotations

import json
import shutil
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from evolvekit.harness.execute import preview, read_job, read_preview, run_study, run_test
from evolvekit.harness.library import create_study
from evolvekit.harness.study import Guardrail, load_study, save_study
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
