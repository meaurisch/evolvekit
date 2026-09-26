"""The app's server and API (`evolvekit/app/server.py`, `api.py`): security,
and the study endpoints, over real HTTP on a free port."""

from __future__ import annotations

import http.client
import json
import socket
from pathlib import Path

import pytest

from evolvekit.app.server import AppServer

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "harnesses" / "demo-tour"
SOLVER = ROOT / "examples" / "cli-solver" / "solver.py"


@pytest.fixture
def app(tmp_path):
    server = AppServer(tmp_path / "home", port=0)
    server.start()
    yield server
    server.stop()


def call(app: AppServer, method: str, path: str, body: bytes | dict | None = None, *, origin: str | None = "own",
         host: str | None = None, headers: dict | None = None) -> tuple[int, object]:
    connection = http.client.HTTPConnection("127.0.0.1", app.port, timeout=120)
    sent = dict(headers or {})
    sent["Host"] = host or f"127.0.0.1:{app.port}"
    if origin == "own":
        sent["Origin"] = f"http://127.0.0.1:{app.port}"
    elif origin is not None:
        sent["Origin"] = origin
    payload = json.dumps(body).encode() if isinstance(body, dict) else body
    if isinstance(body, dict):
        sent["Content-Type"] = "application/json"
    connection.request(method, path, body=payload, headers=sent)
    response = connection.getresponse()
    data = response.read()
    connection.close()
    kind = response.getheader("Content-Type") or ""
    return response.status, (json.loads(data) if kind.startswith("application/json") else data)


# ---------------------------------------------------------------------------
# security
# ---------------------------------------------------------------------------


def test_the_page_is_served(app):
    status, body = call(app, "GET", "/")
    assert status == 200 and b"<html" in body


def test_a_foreign_host_is_refused(app):
    status, body = call(app, "GET", "/api/home", host="evil.example:80")
    assert status == 403 and "127.0.0.1 and localhost" in body["error"]
    assert call(app, "GET", "/api/home", host=f"localhost:{app.port}")[0] == 200


@pytest.mark.parametrize("origin", [None, "http://evil.example", "null", "http://127.0.0.1:1"])
def test_a_change_needs_the_apps_own_origin(app, origin):
    status, body = call(app, "PUT", "/api/settings", {"provider": "azure"}, origin=origin)
    assert status == 403 and "the app's own page" in body["error"]
    assert call(app, "GET", "/api/settings")[1]["provider"] == "openrouter", "nothing changed"


def test_a_cross_site_request_is_refused(app):
    status, _ = call(app, "GET", "/api/home", headers={"Sec-Fetch-Site": "cross-site"})
    assert status == 403


def test_an_unknown_route_is_a_json_404(app):
    status, body = call(app, "GET", "/api/nothing-here")
    assert status == 404 and body == {"error": "not found"}


def test_an_upload_over_the_limit_is_refused_before_it_is_read(app):
    with socket.create_connection(("127.0.0.1", app.port), timeout=10) as sock:
        sock.sendall((f"PUT /api/studies/x/cases/a.json HTTP/1.1\r\nHost: 127.0.0.1:{app.port}\r\n"
                      f"Origin: http://127.0.0.1:{app.port}\r\nContent-Length: {300 * 1024 * 1024}\r\n\r\n").encode())
        received = b""
        while chunk := sock.recv(4096):  # the server closes the connection after refusing
            received += chunk
        answer = received.decode()
    assert answer.startswith("HTTP/1.1 413") and "200 MB" in answer


def test_keys_are_accepted_and_never_sent_back(app, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    status, body = call(app, "PUT", "/api/settings", {"keys": {"OPENROUTER_API_KEY": "sk-or-v1-secret"}})
    assert status == 200 and body["keys"]["OPENROUTER_API_KEY"] is True
    assert "sk-or-v1-secret" not in json.dumps(body)
    assert "sk-or-v1-secret" not in json.dumps(call(app, "GET", "/api/home")[1])


@pytest.mark.parametrize("path", ["/api/studies/..%2F..%2Fx", "/api/studies/..", "/api/studies/C%3A%5Cx",
                                  "/studies/..%2Fx/runs/r/dashboard/", "/api/studies/UPPER"])
def test_paths_cannot_leave_the_library_home(app, path):
    status, _ = call(app, "GET", path)
    assert status == 404


def test_an_uploaded_name_cannot_climb_out_of_the_cases_folder(app, tmp_path):
    _, created = call(app, "POST", "/api/studies", {"harness": "demo-tour", "template": "tune", "name": "Climb"})
    slug = created["slug"]
    status, body = call(app, "PUT", f"/api/studies/{slug}/cases/..%2F..%2Fevil.json", b"{}")
    assert status == 400 and "no folders" in body["error"]
    assert not list(tmp_path.rglob("evil.json"))


# ---------------------------------------------------------------------------
# the home and a study, step by step
# ---------------------------------------------------------------------------


def test_the_home_lists_the_harnesses_and_the_studies(app):
    status, home = call(app, "GET", "/api/home")
    assert status == 200 and home["studies"] == []
    ids = {h["id"] for h in home["harnesses"]}
    assert {"demo-tour", "pyvrp"} <= ids
    status, harness = call(app, "GET", "/api/harnesses/pyvrp")
    assert harness["title"] == "PyVRP" and len(harness["settings"]) == 27 and "route-costs" in harness["templates"]
    assert harness["readme"].startswith("# PyVRP")


def test_a_study_through_its_steps(app):
    status, document = call(app, "POST", "/api/studies", {"harness": "demo-tour", "template": "tune", "name": "Tours"})
    assert status == 200 and document["slug"] == "tours"
    assert {p["step"] for p in document["problems"]} == {2, 3}
    assert document["state"] == {"kind": "draft", "line": "draft · step 1 of 7"}

    status, answer = call(app, "POST", "/api/studies/tours/application", {"path": str(ROOT / "nowhere.py")})
    assert answer["probe"]["ok"] is False and "nothing at" in answer["probe"]["message"]
    status, answer = call(app, "POST", "/api/studies/tours/application", {"path": str(SOLVER)})
    assert answer["probe"]["ok"] is True and answer["study"]["study"]["application"]["version"] == "1.0"

    status, case = call(app, "PUT", "/api/studies/tours/cases/town-40.json", (DEMO / "samples" / "town-40.json").read_bytes())
    assert status == 200 and case["inspected"]["summary"] == "40 stops"
    status, document = call(app, "POST", "/api/studies/tours/samples")
    assert len(document["cases"]) == 4 and document["suggested_test"] == 1
    status, document = call(app, "POST", "/api/studies/tours/split", {"test": 1})
    assert document["study"]["cases"]["test"] == ["cases/county-50.json"]
    assert document["problems"] == []

    status, document = call(app, "PATCH", "/api/studies/tours", {"limits": {"time_per_case_s": 0.5, "retries": 1,
                                                                          "runs_per_case": 1}, "step": 6})
    assert status == 200 and document["study"]["limits"]["time_per_case_s"] == 0.5
    status, body = call(app, "PATCH", "/api/studies/tours", {"harness": {"id": "pyvrp", "version": "1.0.0"}})
    assert status == 400 and "keeps its harness" in body["error"]

    status, plan = call(app, "GET", "/api/studies/tours/plan")
    assert status == 200 and plan["estimated"] is True and plan["summary"]
    status, body = call(app, "DELETE", "/api/studies/tours/cases/town-40.json")
    assert status == 200 and "cases/town-40.json" not in body["study"]["cases"]["training"]
    status, home = call(app, "GET", "/api/home")
    assert [s["slug"] for s in home["studies"]] == ["tours"] and home["studies"][0]["line"] == "draft · step 6 of 7"


def test_the_detection_list_answers_for_a_study(app):
    call(app, "POST", "/api/studies", {"harness": "pyvrp", "template": "tune", "name": "Detect"})
    status, found = call(app, "GET", "/api/detect?study=detect")
    assert status == 200 and isinstance(found, list) and found
    assert all(set(item) == {"path", "ok", "message", "version", "python"} for item in found)
    assert found == sorted(found, key=lambda item: not item["ok"]), "matches first"


def test_a_settings_file_is_checked_on_upload(app):
    call(app, "POST", "/api/studies", {"harness": "pyvrp", "template": "tune", "name": "Inputs"})
    status, body = call(app, "PUT", "/api/studies/inputs/inputs/solver_settings/mine.json", b'{"speed": 1}')
    assert status == 400 and "not a setting of PyVRP" in body["error"]
    status, document = call(app, "PUT", "/api/studies/inputs/inputs/solver_settings/mine.json", b'{"num_neighbours": 30}')
    assert status == 200 and document["study"]["inputs"] == {"solver_settings": "inputs/mine.json"}
