"""The demo harness (`harnesses/demo-tour/`): the example CLI solver behind
the SDK, driven through its runner the way a study drives it."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from evolvekit.harness.compile import compile_study
from evolvekit.harness.manifest import load_harness
from evolvekit.harness.plan import Plan
from evolvekit.harness.sdk import SDK_PATH
from evolvekit.harness.study import study_from_template

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "harnesses" / "demo-tour"
SOLVER = ROOT / "examples" / "cli-solver" / "solver.py"
SAMPLE = DEMO / "samples" / "north-south-30.json"


def _runner(*args: str, cwd: Path = DEMO) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(DEMO / "runner.py"), "--app", str(SOLVER), *args],
        cwd=cwd, capture_output=True, text=True, encoding="utf-8", timeout=120,
    )


def _last_json(completed: subprocess.CompletedProcess) -> dict:
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_the_demo_harness_loads_with_its_templates():
    harness = load_harness(DEMO)
    assert harness.id == "demo-tour" and harness.application.kind == "program"
    assert set(harness.templates) == {"tune", "stop-weights"}
    assert harness.recommended == ["neighbours", "restart_after", "accept_worse", "init"]


@pytest.mark.parametrize("folder", sorted(p for p in (ROOT / "harnesses").iterdir() if (p / "harness.yaml").is_file()))
def test_every_built_in_harness_carries_the_current_sdk(folder):
    assert (folder / "evk_harness.py").read_bytes().replace(b"\r\n", b"\n") == SDK_PATH.read_bytes().replace(b"\r\n", b"\n")


def test_inspect_and_describe():
    assert _last_json(_runner("inspect", "--case", str(SAMPLE))) == {"ok": True, "summary": "30 stops", "tables": {"stops": 30}}
    seeded = _last_json(_runner("inspect", "--case", str(ROOT / "examples" / "cli-solver" / "data" / "a-40.json")))
    assert seeded["summary"] == "40 stops", "the example's own seeded requests are cases too"
    described = _last_json(_runner("describe"))
    assert described["discover"]["version"] == "1.0"
    assert set(described["discover"]["settings"]) == {"neighbours", "restart_after", "accept_worse", "init", "or_opt"}


def test_a_request_that_is_not_one_cannot_be_read(tmp_path):
    (tmp_path / "x.json").write_text('{"nothing": 1}', encoding="utf-8")
    completed = _runner("inspect", "--case", str(tmp_path / "x.json"))
    assert completed.returncode == 3
    assert completed.stderr.strip().splitlines()[-1].startswith("cannot read x.json: not a tour request")


def test_a_stop_weights_study_solves_a_case_end_to_end(tmp_path):
    harness = load_harness(DEMO)
    study = study_from_template(harness, "stop-weights", "North weights")
    study.application_path = str(SOLVER)
    study.training = ["cases/north-south-30.json"]
    study.limits.time_per_case_s = 0.3
    _, study_run = compile_study(study, harness, Plan.fixed())
    (tmp_path / "study-run.json").write_text(json.dumps(study_run), encoding="utf-8")
    (tmp_path / "values.json").write_text(json.dumps({"north_weight": 1.8}), encoding="utf-8")
    result = _last_json(_runner(
        "solve", "--case", str(SAMPLE), "--seed", "1", "--values", "values.json", "--study", "study-run.json",
        "--time-limit", "0.3", "--tables-out", "tables.sqlite", cwd=tmp_path,
    ))
    kpis = result["kpis"]
    assert kpis["tour_length"] > 0 and kpis["longest_leg"] > 0 and kpis["guardrail_violation"] == 0
    db = sqlite3.connect(tmp_path / "tables.sqlite")
    weights = dict(db.execute("SELECT tag, AVG(weight) FROM stops GROUP BY tag").fetchall())
    assert weights == {"north": pytest.approx(1.8), "south": pytest.approx(1.0)}
    assert db.execute("SELECT COUNT(*) FROM tour").fetchone() == (30,)
    length, weighted = db.execute("SELECT length, weighted_length FROM summary").fetchone()
    assert weighted > length, "the solver saw the heavier northern legs"


def test_the_changed_request_can_be_exported(tmp_path):
    harness = load_harness(DEMO)
    study = study_from_template(harness, "stop-weights", "North weights")
    study.application_path, study.training = str(SOLVER), ["cases/a.json"]
    _, study_run = compile_study(study, harness, Plan.fixed())
    (tmp_path / "study-run.json").write_text(json.dumps(study_run), encoding="utf-8")
    (tmp_path / "values.json").write_text(json.dumps({"north_weight": 0.5}), encoding="utf-8")
    completed = _runner("export", "--format", "requests", "--values", "values.json", "--study", "study-run.json",
                        "--case", str(SAMPLE), "--out", "changed.json", cwd=tmp_path)
    assert completed.returncode == 0, completed.stderr
    changed = json.loads((tmp_path / "changed.json").read_text(encoding="utf-8"))
    north = [stop[2] for i, stop in enumerate(changed["stops"]) if changed["tags"][str(i)] == "north"]
    assert north and set(north) == {0.5}
