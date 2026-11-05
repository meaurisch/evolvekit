"""The app's background work and runs (`evolvekit/app/work.py`, `jobs.py`,
`job.py`), through HTTP with the demo harness -- the design's end-to-end test:
create, application, cases, split, preview, a KPI tried out, the plan, the
test run, start, status, results, every download, a study from the result."""

from __future__ import annotations

import html
import http.client
import json
import os
import re
import socket
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from evolvekit.app import jobs
from evolvekit.app.server import AppServer
from evolvekit.events import utc_now
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


def _disposition(app: AppServer, path: str) -> str:
    connection = http.client.HTTPConnection("127.0.0.1", app.port, timeout=120)
    connection.request("GET", path, headers={"Host": f"127.0.0.1:{app.port}"})
    response = connection.getresponse()
    response.read()
    connection.close()
    return response.getheader("Content-Disposition") or ""


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

    checking: list[str] = []

    def ended(body: dict) -> bool:
        if body["phase"] == "check":
            checking.append(body["now"])
        return body["phase"] in ("done", "failed", "stopped")

    final = _until(app, f"/api/studies/{slug}/status", ended)
    assert final["phase"] == "done", final
    # While the check runs, the page says how many runs per case it is on and that it may add more.
    assert all(re.fullmatch(r"The final check: \d+ runs? per case so far on the held-back cases; "
                            r"it adds runs while time remains and the answer is open\.", now) for now in checking), checking
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
    if results["final"]["state"] != "skipped":
        assert results["final"]["decision"] in ("use", "keep", "worse", "single")
    assert {k["name"] for k in results["kpis"]} == {"tour_length", "longest_leg"}
    assert [c["name"] for c in results["changed"]] == ["neighbours", "restart_after", "accept_worse", "init"]
    assert [d["name"] for d in results["downloads"]] == ["settings_json", "settings_flags", "report"]

    status, settings = call(app, "GET", f"/api/studies/{slug}/download/settings_json")
    assert status == 200 and set(settings) == {"neighbours", "restart_after", "accept_worse", "init", "or_opt"}
    status, flags = call(app, "GET", f"/api/studies/{slug}/download/settings_flags")
    assert status == 200 and b"--neighbours" in flags
    status, report = call(app, "GET", f"/api/studies/{slug}/download/report")
    assert status == 200 and html.escape(results["headline"]).encode() in report and b"</html>" in report
    assert _disposition(app, f"/api/studies/{slug}/download/report").startswith("attachment;")
    assert _disposition(app, f"/api/studies/{slug}/download/report?view=1") == "", "?view=1 opens it in the browser"
    assert results["summary_text"].startswith("Tours (") and results["headline"] in results["summary_text"]
    assert results["context"]["time_per_case_s"] == 0.5 and results["context"]["tried"] >= 1
    if results["final"]["state"] == "single":  # the check ran: its one case, side by side
        assert [c["case"] for c in results["by_case"]["held"]] == ["county-50"]
        tour = next(k for k in results["kpis"] if k["name"] == "tour_length")
        assert tour["test"]["start"] is not None and tour["test"]["best"] is not None
    status, body = call(app, "GET", f"/api/studies/{slug}/download/requests")
    assert status == 404 or status == 200

    status, home = call(app, "GET", "/api/home")
    assert home["studies"][0]["kind"] == "finished"

    status, document = call(app, "POST", f"/api/studies/{slug}/next", {"name": "Tours again"})
    assert status == 200 and document["slug"] == "tours-again"
    assert document["study"]["cases"] == {"training": ["cases/north-south-30.json", "cases/town-40.json", "cases/region-60.json"],
                                          "test": ["cases/county-50.json"]}
    assert document["problems"] == [], document["problems"]

    # The run reads the study it was started with: a split changed afterwards
    # does not change what the results say was held back.
    status, body = call(app, "PATCH", f"/api/studies/{slug}", {
        "cases": {"training": ["cases/north-south-30.json", "cases/town-40.json", "cases/county-50.json"],
                  "test": ["cases/region-60.json"]}})
    assert status == 200, body
    status, after = call(app, "GET", f"/api/studies/{slug}/results")
    assert status == 200 and after["cases"] == results["cases"] == {
        "training": ["cases/north-south-30.json", "cases/town-40.json", "cases/region-60.json"],
        "test": ["cases/county-50.json"]}
    assert after["by_case"]["held"] == results["by_case"]["held"]
    if results["final"]["state"] != "skipped":
        assert [c["case"] for c in after["by_case"]["held"]] == ["county-50"]


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


# -- a run is alive when its process is, and that process is the one we started


def test_a_recycled_pid_is_not_a_running_run(tmp_path):
    long_ago = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
    assert jobs.run_alive(tmp_path, os.getpid(), long_ago, socket.gethostname()) is False, "this process is younger than the launch"
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    assert jobs.run_alive(tmp_path, os.getpid(), now, socket.gethostname()) is True
    assert jobs.run_alive(tmp_path, None, now, None) is False


def test_a_run_from_another_computer_is_judged_by_its_heartbeat(tmp_path):
    (tmp_path / "heartbeat.json").write_text(json.dumps({"ts": utc_now()}), encoding="utf-8")
    assert jobs.run_alive(tmp_path, 1, utc_now(), "elsewhere") is True
    old = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(timespec="milliseconds")
    (tmp_path / "heartbeat.json").write_text(json.dumps({"ts": old}), encoding="utf-8")
    assert jobs.run_alive(tmp_path, 1, utc_now(), "elsewhere") is False
    (tmp_path / "heartbeat.json").unlink()
    assert jobs.run_alive(tmp_path, 1, utc_now(), "elsewhere") is False, "no beat at all is not alive"


def test_a_dead_run_reads_as_failed_with_the_last_thing_it_wrote(tmp_path):
    run_dir = tmp_path / "runs" / "r"; run_dir.mkdir(parents=True)
    (run_dir / "launch.json").write_text(json.dumps({"pid": os.getpid(), "started_at": "2020-01-01T00:00:00+00:00",
                                                      "host": socket.gethostname()}), encoding="utf-8")
    (run_dir / "job.json").write_text(json.dumps({"phase": "search", "pid": os.getpid(), "started_at": "2020-01-01T00:00:00+00:00"}), encoding="utf-8")
    assert jobs.phase(run_dir)["phase"] == "failed"


def test_a_run_stopped_before_it_began_offers_no_final_check(app):
    slug = _ready_study(app, "Early stop")
    run_dir = app.home.study_root(slug) / "runs" / "20260101-000000"
    run_dir.mkdir(parents=True)
    (run_dir / "job.json").write_text(json.dumps({
        "phase": "stopped", "pid": 2 ** 22 + 7, "started_at": "2026-01-01T00:00:00+00:00", "finished_at": "2026-01-01T00:00:01+00:00",
        "search": {"stop_reason": "stopped on request before the search began", "generations": 0, "aborted": False}}), encoding="utf-8")
    status, answer = call(app, "GET", f"/api/studies/{slug}/status")
    assert status == 200 and answer["phase"] == "stopped" and answer["can_check"] is False, answer
    assert answer["sentence"] == "Stopped. stopped on request before the search began."
    assert answer["can_resume"] is False, "nothing was searched: there is nothing to continue from"


# -- a run that died resumes where it was


@pytest.mark.slow
def test_the_app_offers_to_continue_a_run_that_died(app):
    slug = _ready_study(app, "Continue me")
    call(app, "PATCH", f"/api/studies/{slug}", {"plan": {"auto": False, "children": 2, "generations": 4}})
    status, started = call(app, "POST", f"/api/studies/{slug}/start")
    assert status == 200 and "resumed" not in started
    _until(app, f"/api/studies/{slug}/status", lambda b: b["phase"] == "search" and b["rounds"]["done"] >= 1, timeout=120)
    call(app, "POST", f"/api/studies/{slug}/stop")
    s = _until(app, f"/api/studies/{slug}/status", lambda b: b["phase"] in ("done", "failed", "stopped"), timeout=120)
    assert s["phase"] == "stopped" and s["can_resume"] is True, s
    status, body = call(app, "POST", f"/api/studies/{slug}/start", {"resume": s["run"]})
    assert status == 200 and body == {"run": s["run"], "resumed": True}
    status, again = call(app, "POST", f"/api/studies/{slug}/start", {"resume": s["run"]})
    assert status == 409, "it is running again: not twice"
    final = _until(app, f"/api/studies/{slug}/status", lambda b: b["phase"] in ("done", "failed"), timeout=180)
    assert final["phase"] == "done" and final["can_resume"] is False, final
    assert final["rounds"]["done"] == 4 and final["time"]["used_s"] > 0
    listed = call(app, "GET", f"/api/studies/{slug}")[1]["runs"]
    assert [r["id"] for r in listed] == [s["run"]], "the same run, continued: not a second one"


def test_a_resumed_run_is_starting_while_its_job_is_still_the_old_one(tmp_path):
    # Between the launch of the resume and the first word of the new process,
    # job.json still says the old run ended; the live, younger launch says it
    # is starting again -- so nothing launches it twice meanwhile.
    run_dir = tmp_path / "runs" / "r"; run_dir.mkdir(parents=True)
    now = utc_now()
    (run_dir / "job.json").write_text(json.dumps({"phase": "stopped", "pid": 2 ** 22 + 7, "started_at": "2026-01-01T00:00:00+00:00",
                                                   "finished_at": "2026-01-01T00:10:00+00:00", "search": {"generations": 1}}), encoding="utf-8")
    (run_dir / "launch.json").write_text(json.dumps({"pid": os.getpid(), "started_at": now, "host": socket.gethostname()}), encoding="utf-8")
    seen = jobs.phase(run_dir)
    assert seen["phase"] == "starting" and seen["started_at"] == "2026-01-01T00:00:00+00:00", seen
    assert jobs.running(tmp_path) is True
    # The launch that wrote the old job is the old process itself: not a resume.
    (run_dir / "launch.json").write_text(json.dumps({"pid": 2 ** 22 + 7, "started_at": now, "host": socket.gethostname()}), encoding="utf-8")
    assert jobs.phase(run_dir)["phase"] == "stopped"


# -- one study at a time, and unique run names


def _fake_active_run(app: AppServer, slug: str, run: str = "20260101-000000") -> Path:
    """A run of `slug` that reads as starting: a launch by this very process,
    which is alive, has written no job yet -- and nothing is really launched."""
    run_dir = app.home.study_root(slug) / "runs" / run
    run_dir.mkdir(parents=True)
    (run_dir / "launch.json").write_text(json.dumps({"pid": os.getpid(), "started_at": utc_now(), "host": socket.gethostname()}),
                                         encoding="utf-8")
    return run_dir


@pytest.mark.slow
def test_a_second_study_waits_its_turn(app):
    first, second = _ready_study(app, "First"), _ready_study(app, "Second")
    status, started = call(app, "POST", f"/api/studies/{first}/start")
    assert status == 200 and "queued" not in started
    status, body = call(app, "POST", f"/api/studies/{second}/start")
    assert status == 200 and body["queued"] is True, body
    s = call(app, "GET", f"/api/studies/{second}/status")[1]
    assert s["phase"] == "queued" and s["sentence"] == "Waiting for 'First' to finish.", s
    assert s["time"]["left_s"] is None and s["can_resume"] is False and s["can_check"] is False
    lines = {st["slug"]: st for st in call(app, "GET", "/api/home")[1]["studies"]}
    assert lines[second]["kind"] == "queued" and lines[second]["line"] == "queued · after First"
    assert call(app, "POST", f"/api/studies/{second}/start")[0] == 409, "queued is running, as far as a second start goes"
    assert call(app, "PATCH", f"/api/studies/{second}", {"plan": {"auto": True}})[0] == 409, "a queued study is not edited"
    _until(app, f"/api/studies/{first}/status", lambda b: b["phase"] == "done", timeout=180)
    s = _until(app, f"/api/studies/{second}/status", lambda b: b["phase"] != "queued", timeout=30)  # the ticker launched it
    assert s["phase"] in ("starting", "search", "check", "done"), s
    assert not (app.home.study_root(second) / "runs" / body["run"] / "queued.json").exists()
    _until(app, f"/api/studies/{second}/status", lambda b: b["phase"] in ("done", "failed", "stopped"), timeout=180)


def test_a_queued_run_can_be_taken_back(app):
    first, second = _ready_study(app, "First"), _ready_study(app, "Second")
    _fake_active_run(app, first)
    assert app.home.active_run() == (first, "20260101-000000")
    status, body = call(app, "POST", f"/api/studies/{second}/start")
    assert status == 200 and body["queued"] is True, body
    run_dir = app.home.study_root(second) / "runs" / body["run"]
    assert (run_dir / "queued.json").is_file() and jobs.phase(run_dir)["phase"] == "queued"
    assert jobs.launch_queued(app.home) is None, "the first study is active: nothing is launched"
    assert (run_dir / "queued.json").is_file()
    assert call(app, "PATCH", f"/api/studies/{second}", {"plan": {"auto": True}})[0] == 409
    status, body = call(app, "POST", f"/api/studies/{second}/stop")
    assert status == 200 and body == {"stopping": False, "dequeued": True}, body
    assert not run_dir.exists()
    document = call(app, "GET", f"/api/studies/{second}")[1]
    assert document["runs"] == [] and document["state"]["kind"] == "draft"
    assert call(app, "PATCH", f"/api/studies/{second}", {"plan": {"auto": True}})[0] == 200, "editable again"


def test_two_starts_in_one_second_get_two_visible_run_ids(app, monkeypatch):
    fixed = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(jobs, "_now", lambda: fixed)

    def launched_and_done(home, root, run_dir, extra):  # a run that finished within the second
        (run_dir / "job.json").write_text(json.dumps({"phase": "done", "pid": 2 ** 22 + 7, "started_at": fixed.isoformat(),
                                                       "finished_at": fixed.isoformat()}), encoding="utf-8")
        return 2 ** 22 + 7

    monkeypatch.setattr(jobs, "_launch", launched_and_done)
    slug, other = _ready_study(app, "Twice"), _ready_study(app, "Other")
    status, one = call(app, "POST", f"/api/studies/{slug}/start")
    assert status == 200 and one == {"run": fixed.astimezone().strftime("%Y%m%d-%H%M%S")}
    _fake_active_run(app, other)  # so the second start waits, in a directory of its own
    status, two = call(app, "POST", f"/api/studies/{slug}/start")
    assert status == 200 and two == {"run": one["run"] + "-2", "queued": True}, two
    listed = call(app, "GET", f"/api/studies/{slug}")[1]["runs"]
    assert [r["id"] for r in listed] == [one["run"], two["run"]] and [r["phase"] for r in listed] == ["done", "queued"]
    assert all(jobs._RUN.match(r["id"]) and "~" not in r["id"] for r in listed)  # noqa: SLF001
    assert jobs.latest_run(app.home.study_root(slug)) == two["run"]
