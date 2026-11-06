"""Two studies, or two runs, side by side (`evolvekit/app/compare.py`): built
from each run's results, aligned by label, on hand-written finished runs."""

from __future__ import annotations

import pytest

from evolvekit.app import AppError, compare
from evolvekit.app.server import AppServer
from test_app_results import document_of, finished_run, study_with_runs
from test_app_server import call


def test_two_finished_runs_compare_side_by_side(tmp_path):
    root, home = study_with_runs(tmp_path, "Tours A")
    finished_run(root / "runs" / "20260101-000000", decision="use")
    finished_run(root / "runs" / "20260102-000000", started="2026-01-02T00:00:00+00:00", decision="keep", mean=0.4)
    other = home.create_study("demo-tour", "tune", "Tours B")
    home.use_samples(other)
    home.split(other, 1)
    finished_run(home.study_root(other) / "runs" / "20260103-000000", check=False)

    both = compare.compare(home, root.name, other, document_of)
    assert both["same_goal"] is True
    a, b = both["a"], both["b"]
    assert (a["slug"], a["name"], a["run"], a["harness"]) == (root.name, "Tours A", "20260102-000000", "Demo tour")
    assert a["goal"] == {"says": "tour length", "direction": "lower"}
    assert a["final"]["state"] == "unclear" and a["final"]["decision"] == "keep"
    assert a["final"]["line"].startswith("Not proven on 1 held-back case")
    assert a["final"]["chance"] == pytest.approx(0.97) and a["final"]["ci95"] == [pytest.approx(-0.8), pytest.approx(1.6)]
    assert a["final"]["mean"] is not None and a["final"]["n"] == 1, "the estimate itself, for the range strip"
    assert a["held"] == [{"case": "cases/town-40.json", "start": 50.0, "best": pytest.approx(49.8), "change_pct": pytest.approx(0.4)}]
    assert [c["name"] for c in a["changes"]] == ["neighbours", "restart_after", "accept_worse", "init"]
    assert next(c for c in a["changes"] if c["name"] == "restart_after") == {
        "name": "restart_after", "label": "Stalled moves before a restart", "old_text": "2,000", "new_text": "13,000", "changed": True}
    assert a["setup"]["hours"] == 0.2 and a["setup"]["combinations"] == 2 and a["setup"]["time_per_case_s"] > 0
    assert a["headline"].startswith("The best settings found give 4.2 % lower tour length")
    # A bare slug is its latest run; a run named is that run.
    assert compare.compare(home, f"{root.name}/20260101-000000", other, document_of)["a"]["final"]["state"] == "confirmed"
    # Review focus 5: a run without a final check says so, and compares all the same.
    assert b["run"] == "20260103-000000" and b["final"]["state"] == "missing"
    assert b["final"]["line"] == "There is no final check for this run." and b["held"] == [] and b["final"]["ci95"] is None


def test_a_side_that_does_not_exist_is_404(tmp_path):
    root, home = study_with_runs(tmp_path, "Tours A")
    finished_run(root / "runs" / "20260101-000000")
    for a, b in ((root.name, "nope"), ("nope", root.name), (f"{root.name}/20991231-000000", root.name), ("", root.name)):
        with pytest.raises(AppError) as caught:
            compare.compare(home, a, b, document_of)
        assert caught.value.status == 404, (a, b, str(caught.value))
    # A study not yet run has no latest run to compare.
    empty = home.create_study("demo-tour", "tune", "Empty")
    with pytest.raises(AppError) as caught:
        compare.compare(home, root.name, empty, document_of)
    assert caught.value.status == 404 and "not been run" in str(caught.value)


@pytest.fixture
def app(tmp_path):
    server = AppServer(tmp_path / "home", port=0)
    server.start()
    yield server
    server.stop()


def test_the_compare_route(app):
    _, first = call(app, "POST", "/api/studies", {"harness": "demo-tour", "template": "tune", "name": "First"})
    _, second = call(app, "POST", "/api/studies", {"harness": "demo-tour", "template": "tune", "name": "Second"})
    for document in (first, second):
        call(app, "POST", f"/api/studies/{document['slug']}/samples")
        call(app, "POST", f"/api/studies/{document['slug']}/split", {"test": 1})
    finished_run(app.home.study_root(first["slug"]) / "runs" / "20260101-000000", decision="use")
    finished_run(app.home.study_root(second["slug"]) / "runs" / "20260102-000000", check=False)
    status, body = call(app, "GET", f"/api/compare?a={first['slug']}/20260101-000000&b={second['slug']}")
    assert status == 200, body
    assert body["same_goal"] is True and body["a"]["final"]["state"] == "confirmed" and body["b"]["final"]["state"] == "missing"
    assert body["a"]["name"] == "First" and body["b"]["run"] == "20260102-000000"
    assert call(app, "GET", f"/api/compare?a={first['slug']}&b=nope")[0] == 404
    assert call(app, "GET", "/api/compare?a=" + first["slug"])[0] == 404
