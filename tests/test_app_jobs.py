"""The app's background work and runs (`evolvekit/app/work.py`, `jobs.py`,
`job.py`), through HTTP with the demo harness -- the design's end-to-end test:
create, application, cases, split, preview, a KPI tried out, the plan, the
test run, start, status, results, every download, a study from the result."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from evolvekit.app.server import AppServer
from test_app_server import call

ROOT = Path(__file__).resolve().parents[1]
SOLVER = ROOT / "examples" / "cli-solver" / "solver.py"


@pytest.fixture
def app(tmp_path):
    server = AppServer(tmp_path / "home", port=0)
    server.start()
    yield server
    server.stop()


def _until(app: AppServer, path: str, done, timeout: float = 180.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        status, body = call(app, "GET", path)
        assert status == 200, body
        if done(body):
            return body
        assert time.monotonic() < deadline, f"{path} did not get there: {body}"
        time.sleep(0.3)


def _ready_study(app: AppServer, name: str = "Tours") -> str:
    _, document = call(app, "POST", "/api/studies", {"harness": "demo-tour", "template": "tune", "name": name})
    slug = document["slug"]
    call(app, "POST", f"/api/studies/{slug}/application", {"path": str(SOLVER)})
    call(app, "POST", f"/api/studies/{slug}/samples")
    call(app, "POST", f"/api/studies/{slug}/split", {"test": 1})
    status, document = call(app, "PATCH", f"/api/studies/{slug}", {
        "limits": {"time_per_case_s": 0.5, "retries": 1, "runs_per_case": 1},
        "budget": {"hours": 0.05, "ai": {"enabled": False, "max_usd": 2.0}},
        "plan": {"auto": False, "children": 2, "generations": 2},
    })
    assert status == 200 and document["problems"] == [], document["problems"]
    return slug


def test_the_preview_the_kpi_box_and_the_test_run(app):
    slug = _ready_study(app)
    status, state = call(app, "POST", f"/api/studies/{slug}/preview")
    assert status == 200 and state["state"] == "running"
    preview = _until(app, f"/api/studies/{slug}/preview", lambda b: b["state"] != "running")
    assert preview["state"] == "done", preview
    assert preview["result"]["case"] == "cases/north-south-30.json" and preview["result"]["kpis"]["tour_length"] > 0

    status, tried = call(app, "POST", f"/api/studies/{slug}/kpis/try",
                         {"sql": "SELECT COUNT(*) FROM stops WHERE tag = 'north'", "rows_sql": "SELECT stop_id, x, y FROM stops WHERE tag = 'north'"})
    assert status == 200 and tried["value"] > 0 and tried["rows"]["columns"] == ["stop_id", "x", "y"]
    assert len(tried["rows"]["rows"]) == tried["value"]
    status, body = call(app, "POST", f"/api/studies/{slug}/kpis/try", {"sql": "DELETE FROM stops"})
    assert status == 400 and "may only read" in body["error"]
    status, body = call(app, "POST", f"/api/studies/{slug}/kpis/try", {"sql": "SELECT 'many'"})
    assert status == 400 and "not a number" in body["error"]

    status, plan = call(app, "GET", f"/api/studies/{slug}/plan")
    assert plan["estimated"] is False and plan["generations"] == 2 and plan["children"] == 2

    call(app, "POST", f"/api/studies/{slug}/test-run")
    test_run = _until(app, f"/api/studies/{slug}/test-run", lambda b: b["state"] != "running")
    assert test_run["state"] == "done", test_run
    assert test_run["result"]["kpis"]["tour_length"] > 0 and test_run["result"]["guardrails_hold"] is True


@pytest.mark.slow
def test_a_run_from_start_to_results_and_downloads(app, tmp_path):
    slug = _ready_study(app)
    status, started = call(app, "POST", f"/api/studies/{slug}/start")
    assert status == 200, started
    run = started["run"]
    status, body = call(app, "POST", f"/api/studies/{slug}/start")
    assert status == 409 and "already running" in body["error"]
    status, body = call(app, "PATCH", f"/api/studies/{slug}", {"step": 3})
    assert status == 409, "a running study does not change"

    final = _until(app, f"/api/studies/{slug}/status", lambda b: b["phase"] in ("done", "failed", "stopped"))
    assert final["phase"] == "done", final
    assert final["sentence"] == "Finished." and final["goal"]["kpi"] == "tour_length"
    assert final["dashboard"] == f"/studies/{slug}/runs/{run}/dashboard/"
    status, page = call(app, "GET", final["dashboard"])
    assert status == 200 and b"<html" in page
    status, document = call(app, "GET", final["dashboard"] + "api/status")
    assert status == 200 and document["health"]["state"] == "finished"

    status, results = call(app, "GET", f"/api/studies/{slug}/results")
    assert status == 200, results
    assert results["headline"].startswith(("The best settings found give", "No settings found beat"))
    assert results["final"]["state"] in ("single", "confirmed", "unclear", "worse", "skipped")
    assert {k["name"] for k in results["kpis"]} == {"tour_length", "longest_leg"}
    assert [c["name"] for c in results["changed"]] == ["neighbours", "restart_after", "accept_worse", "init"]
    assert [d["name"] for d in results["downloads"]] == ["settings_json", "settings_flags", "report"]

    status, settings = call(app, "GET", f"/api/studies/{slug}/download/settings_json")
    assert status == 200 and set(settings) == {"neighbours", "restart_after", "accept_worse", "init", "or_opt"}
    status, flags = call(app, "GET", f"/api/studies/{slug}/download/settings_flags")
    assert status == 200 and b"--neighbours" in flags
    status, report = call(app, "GET", f"/api/studies/{slug}/download/report")
    assert status == 200 and results["headline"].encode() in report and b"</html>" in report
    status, body = call(app, "GET", f"/api/studies/{slug}/download/requests")
    assert status == 404 or status == 200

    status, home = call(app, "GET", "/api/home")
    assert home["studies"][0]["kind"] == "finished"

    status, document = call(app, "POST", f"/api/studies/{slug}/next", {"name": "Tours again"})
    assert status == 200 and document["slug"] == "tours-again"
    assert document["study"]["cases"] == {"training": ["cases/north-south-30.json", "cases/town-40.json", "cases/region-60.json"],
                                          "test": ["cases/county-50.json"]}
    assert document["problems"] == [], document["problems"]


@pytest.mark.slow
def test_a_stopped_run_says_so(app):
    slug = _ready_study(app, "Stop me")
    call(app, "PATCH", f"/api/studies/{slug}", {"plan": {"auto": False, "children": 4, "generations": 40},
                                                  "budget": {"hours": 0.2, "ai": {"enabled": False, "max_usd": 2.0}}})
    status, started = call(app, "POST", f"/api/studies/{slug}/start")
    assert status == 200
    _until(app, f"/api/studies/{slug}/status", lambda b: b["phase"] == "search" and b["rounds"]["done"] >= 1, timeout=120)
    status, stopping = call(app, "POST", f"/api/studies/{slug}/stop")
    assert status == 200 and stopping["stopping"] is True
    final = _until(app, f"/api/studies/{slug}/status", lambda b: b["phase"] in ("done", "failed", "stopped"), timeout=120)
    assert final["phase"] == "stopped", final
    assert final["can_check"] is True
    status, body = call(app, "POST", f"/api/studies/{slug}/stop")
    assert status == 409


def test_a_run_that_cannot_start_says_what_is_missing(app):
    call(app, "POST", "/api/studies", {"harness": "demo-tour", "template": "tune", "name": "Empty"})
    status, body = call(app, "POST", "/api/studies/empty/start")
    assert status == 409 and body["error"].startswith("the study is not ready yet: application:")


def test_a_run_whose_process_died_without_a_word_is_reported_from_its_log(app, tmp_path):
    slug = _ready_study(app, "Dead")
    run_dir = app.home.study_root(slug) / "runs" / "20260101-000000"
    run_dir.mkdir(parents=True)
    (run_dir / "launch.json").write_text(json.dumps({"pid": 2 ** 22 + 7, "started_at": "2026-01-01T00:00:00+00:00"}))
    (run_dir / "job.log").write_text("Traceback ...\nModuleNotFoundError: No module named 'yaml'\n")
    status, answer = call(app, "GET", f"/api/studies/{slug}/status")
    assert answer["phase"] == "failed" and "No module named 'yaml'" in answer["error"]
