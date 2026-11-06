"""The app's server and API (`evolvekit/app/server.py`, `api.py`): security,
and the study endpoints, over real HTTP on a free port."""

from __future__ import annotations

import http.client
import json
import socket
import time
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
         host: str | None = None, headers: dict | None = None, token: str | None = "own") -> tuple[int, object]:
    """One request, as the app's own page would make it: `token="own"` sends
    the server's key in `X-Evolvekit-Token`; `None` sends no key."""
    connection = http.client.HTTPConnection("127.0.0.1", app.port, timeout=120)
    sent = dict(headers or {})
    sent["Host"] = host or f"127.0.0.1:{app.port}"
    if origin == "own":
        sent["Origin"] = f"http://127.0.0.1:{app.port}"
    elif origin is not None:
        sent["Origin"] = origin
    if token == "own":
        sent["X-Evolvekit-Token"] = app.token
    elif token is not None:
        sent["X-Evolvekit-Token"] = token
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


def _raw(app: AppServer, request: str) -> str:
    """Send `request` as written and read until the server closes the connection
    (it does after a refusal): what came back, decoded."""
    with socket.create_connection(("127.0.0.1", app.port), timeout=10) as sock:
        sock.sendall(request.encode())
        received = b""
        while chunk := sock.recv(4096):
            received += chunk
    return received.decode()


def test_an_upload_over_the_limit_is_refused_before_it_is_read(app):
    answer = _raw(app, f"PUT /api/studies/x/cases/a.json HTTP/1.1\r\nHost: 127.0.0.1:{app.port}\r\n"
                       f"Origin: http://127.0.0.1:{app.port}\r\nX-Evolvekit-Token: {app.token}\r\n"
                       f"Content-Length: {300 * 1024 * 1024}\r\n\r\n")
    assert answer.startswith("HTTP/1.1 413") and "200 MB" in answer


# -- the per-launch key ------------------------------------------------------


def test_the_app_opens_at_an_address_that_carries_its_key(app):
    assert len(app.token) >= 32 and app.url_with_token == f"{app.url}?token={app.token}"
    other = AppServer(app.home.root.parent / "other", port=0)
    try:
        assert other.token != app.token, "two launches never share a key"
    finally:
        other.stop()


def test_a_request_without_the_token_is_refused_before_its_body_is_read(app):
    # The right Origin, the right Host, no key, and a body that never comes: the refusal
    # must arrive at once. Were the body read first, `recv` would wait for it and time out.
    started = time.monotonic()
    answer = _raw(app, f"POST /api/studies HTTP/1.1\r\nHost: 127.0.0.1:{app.port}\r\n"
                       f"Origin: http://127.0.0.1:{app.port}\r\nContent-Type: application/json\r\n"
                       f"Content-Length: {1024 * 1024}\r\n\r\n")
    assert answer.startswith("HTTP/1.1 403") and "open the app from the address it printed" in answer
    assert time.monotonic() - started < 5
    assert call(app, "GET", "/api/home")[1]["studies"] == [], "nothing was created"


@pytest.mark.parametrize("wrong", [None, "", "not-the-key"])
def test_a_wrong_or_missing_token_is_refused_on_every_method(app, wrong):
    status, body = call(app, "GET", "/api/home", token=wrong)
    assert status == 403 and body == {"error": "refused: open the app from the address it printed, which carries its key"}
    status, body = call(app, "POST", "/api/studies", {"harness": "demo-tour", "template": "tune", "name": "No"}, token=wrong)
    assert status == 403 and "carries its key" in body["error"]
    assert call(app, "GET", "/api/home")[1]["studies"] == []


def test_the_token_is_accepted_as_a_header_or_in_the_query(app):
    assert call(app, "GET", "/api/home", token=app.token)[0] == 200
    assert call(app, "GET", f"/api/home?token={app.token}", token=None)[0] == 200
    assert call(app, "GET", "/api/home?token=wrong", token=None)[0] == 403
    assert call(app, "GET", "/api/home", token=None, headers={"x-evolvekit-token": app.token})[0] == 200, "header names are case-insensitive"


def test_the_page_itself_needs_no_token(app):
    status, body = call(app, "GET", "/", token=None)
    assert status == 200 and b"<html" in body
    assert call(app, "GET", "/index.html", token=None)[0] == 200
    assert call(app, "GET", "/robots.txt", token=None)[0] == 403, "the page and its own files are all that is served without the key"


def test_the_pages_own_files_are_served_without_the_key(app):
    status, body = call(app, "GET", "/static/css/tokens.css", token=None)
    assert status == 200 and b"--surface-page" in body
    connection = http.client.HTTPConnection("127.0.0.1", app.port, timeout=30)
    connection.request("GET", "/static/fonts/Overpass-Variable.woff2", headers={"Host": f"127.0.0.1:{app.port}"})
    response = connection.getresponse()
    data = response.read()
    connection.close()
    assert response.status == 200 and response.getheader("Content-Type") == "font/woff2" and data[:4] == b"wOF2"
    assert response.getheader("X-Content-Type-Options") == "nosniff"


@pytest.mark.parametrize("path", [
    "/static/../server.py", "/static/%2e%2e/server.py", "/static/css/..%2f..%2fserver.py", "/static/app.html",
    "/static/css/missing.css", "/static/css", "/static/", "/static/C:/Windows/win.ini", "/static//etc/passwd",
    "/static/.hidden.css", "/static/css/tokens.css.bak",
])
def test_nothing_outside_the_pages_own_files_is_served(app, path):
    status, _ = call(app, "GET", path, token=None)
    assert status == 404


def test_the_pages_own_files_cannot_be_changed(app):
    assert call(app, "POST", "/static/css/tokens.css", b"x", token=None)[0] == 404
    assert call(app, "DELETE", "/static/css/tokens.css", token=None)[0] == 404
    assert (Path(__file__).resolve().parents[1] / "evolvekit/app/static/css/tokens.css").is_file()


def test_the_dashboard_needs_the_token(app):
    _, doc = call(app, "POST", "/api/studies", {"harness": "demo-tour", "template": "tune", "name": "Dash"})
    (app.home.study_root(doc["slug"]) / "runs" / "20260101-000000").mkdir(parents=True)
    dashboard = f"/studies/{doc['slug']}/runs/20260101-000000/dashboard/"
    status, body = call(app, "GET", dashboard, token=None)
    assert status == 403 and "carries its key" in body["error"]
    assert call(app, "GET", f"{dashboard}api/status", token=None)[0] == 403
    status, body = call(app, "GET", f"{dashboard}?token={app.token}", token=None)
    assert status == 200 and b"<html" in body, "the link the page builds carries the key in its query"
    assert call(app, "GET", f"{dashboard}api/status?token={app.token}", token=None)[0] == 200


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


def test_a_thin_plan_offers_to_tune_only_what_matters_most(app):
    call(app, "POST", "/api/studies", {"harness": "demo-tour", "template": "tune", "name": "Thin"})
    call(app, "POST", "/api/studies/thin/application", {"path": str(SOLVER)})
    call(app, "POST", "/api/studies/thin/samples")
    call(app, "PATCH", "/api/studies/thin", {"plan": {"auto": False, "children": 2, "rounds": 2}})
    status, plan = call(app, "GET", "/api/studies/thin/plan")
    assert status == 200 and plan["rounds"] == 2 and plan["generations"] == 4, "rounds as shown; the engine gets headroom"
    assert plan["warning"].startswith("About 4 combinations for 4 things to tune is thin")
    (fewer,) = [fix for fix in plan["fixes"] if fix["kind"] == "fewer"]
    assert fewer["label"] == "Tune only the 2 that matter most" and len(fewer["keep"]) == 2
    status, document = call(app, "POST", "/api/studies/thin/tune-only", {"keep": fewer["keep"]})
    assert status == 200
    tuned = [name for name, choice in document["study"]["vary"]["settings"].items() if choice == "tune" or (isinstance(choice, dict) and choice.get("tune"))]
    assert tuned == fewer["keep"]
    status, body = call(app, "POST", "/api/studies/thin/tune-only", {"keep": ["not_a_setting"]})
    assert status == 400 and "name some of the settings the study tunes" in body["error"]


def test_cases_come_from_a_folder_on_this_computer(app, tmp_path):
    call(app, "POST", "/api/studies", {"harness": "demo-tour", "template": "tune", "name": "Folder"})
    call(app, "POST", "/api/studies/folder/application", {"path": str(SOLVER)})
    folder = tmp_path / "handover" / "cases"
    (folder / "deeper").mkdir(parents=True)
    for name in ("town-40.json", "county-50.json"):
        (folder / name).write_bytes((DEMO / "samples" / name).read_bytes())
    (folder / "deeper" / "region 60.json").write_bytes((DEMO / "samples" / "region-60.json").read_bytes())
    (folder / "notes.txt").write_text("not a case")
    status, answer = call(app, "POST", "/api/studies/folder/import", {"folder": str(folder)})
    assert status == 200, answer
    assert sorted(answer["imported"]["added"]) == ["county-50.json", "region-60.json", "town-40.json"]
    assert all(c["inspected"]["ok"] for c in answer["study"]["cases"])
    for bad, says in (("relative/path", "full path"), (str(tmp_path / "nowhere"), "no folder"), (str(tmp_path), "holds no .json")):
        if says == "holds no .json":
            (tmp_path / "empty").mkdir()
            bad = str(tmp_path / "empty")
        status, body = call(app, "POST", "/api/studies/folder/import", {"folder": bad})
        assert status == 400 and says in body["error"], body


def test_a_harness_is_installed_from_a_path_on_this_computer(app, tmp_path):
    from evolvekit.harness.library import pack_harness

    archive = pack_harness(DEMO, tmp_path)
    status, harness = call(app, "POST", "/api/harnesses", {"path": str(archive)})
    assert status == 200 and harness["id"] == "demo-tour"
    status, home = call(app, "GET", "/api/home")
    assert any(h["id"] == "demo-tour" and not h["built_in"] for h in home["harnesses"]), "the installed copy is listed"
    status, body = call(app, "POST", "/api/harnesses", {"path": "pyvrp-1.0.0.zip"})
    assert status == 400 and "full path" in body["error"]


def test_the_detection_list_answers_for_a_study(app):
    call(app, "POST", "/api/studies", {"harness": "pyvrp", "template": "tune", "name": "Detect"})
    status, found = call(app, "POST", "/api/detect?study=detect")
    assert status == 200 and isinstance(found, list) and found
    assert all(set(item) == {"path", "ok", "message", "version", "python"} for item in found)
    assert found == sorted(found, key=lambda item: not item["ok"]), "matches first"


def test_detection_looks_around_the_machine_so_it_is_not_a_get(app):
    call(app, "POST", "/api/studies", {"harness": "pyvrp", "template": "tune", "name": "Detect"})
    assert call(app, "GET", "/api/detect?study=detect")[0] == 404


def test_a_settings_file_is_checked_on_upload(app):
    call(app, "POST", "/api/studies", {"harness": "pyvrp", "template": "tune", "name": "Inputs"})
    status, body = call(app, "PUT", "/api/studies/inputs/inputs/solver_settings/mine.json", b'{"speed": 1}')
    assert status == 400 and "not a setting of PyVRP" in body["error"]
    status, document = call(app, "PUT", "/api/studies/inputs/inputs/solver_settings/mine.json", b'{"num_neighbours": 30}')
    assert status == 200 and document["study"]["inputs"] == {"solver_settings": "inputs/mine.json"}


# ---------------------------------------------------------------------------
# the status document
# ---------------------------------------------------------------------------


def test_the_status_document_is_rebuilt_only_when_a_run_file_changed(tmp_path, monkeypatch):
    built = []
    monkeypatch.setattr("evolvekit.status.build_status", lambda run_dir: built.append(1) or {"n": len(built)})
    server = AppServer(tmp_path / "home", port=0)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "events.jsonl").write_text("{}\n", encoding="utf-8")
    assert server.status_document(run_dir) == server.status_document(run_dir) and len(built) == 1
    time.sleep(1.2)  # well past the old once-a-second rule: nothing changed, nothing is rebuilt
    assert server.status_document(run_dir) == {"n": 1} and len(built) == 1
    time.sleep(0.01)
    (run_dir / "events.jsonl").write_text("{}\n{}\n", encoding="utf-8")
    assert server.status_document(run_dir) == {"n": 2} and len(built) == 2
    (run_dir / "heartbeat.json").write_text('{"ts": "2026-01-01T00:00:00+00:00"}', encoding="utf-8")
    assert server.status_document(run_dir) == {"n": 3} and len(built) == 3, "a heartbeat that appears is a change"
    (run_dir / "runs.jsonl").write_text("{}\n", encoding="utf-8")
    assert server.status_document(run_dir) == {"n": 4} and len(built) == 4
    assert server.status_document(run_dir) == {"n": 4} and len(built) == 4


# ---------------------------------------------------------------------------
# managing studies
# ---------------------------------------------------------------------------


def test_a_study_can_be_trashed_restored_duplicated_and_archived(app):
    status, created = call(app, "POST", "/api/studies", {"harness": "demo-tour", "template": "tune", "name": "Keep me"})
    assert status == 200 and created["slug"] == "keep-me"
    call(app, "POST", "/api/studies/keep-me/application", {"path": str(SOLVER)})
    call(app, "POST", "/api/studies/keep-me/samples")

    status, copy = call(app, "POST", "/api/studies/keep-me/duplicate", {"name": "Keep me too"})
    assert status == 200 and copy["slug"] == "keep-me-too" and copy["study"]["name"] == "Keep me too"
    assert [c["name"] for c in copy["cases"]] == [c["name"] for c in call(app, "GET", "/api/studies/keep-me")[1]["cases"]]

    status, document = call(app, "PATCH", "/api/studies/keep-me-too", {"archived": True})
    assert status == 200 and document["slug"] == "keep-me-too"
    status, home = call(app, "GET", "/api/home")
    archived = {s["slug"]: s["archived"] for s in home["studies"]}
    assert archived == {"keep-me": False, "keep-me-too": True} and home["trash"] == []
    status, document = call(app, "PATCH", "/api/studies/keep-me-too", {"archived": False, "name": "Keep me 2"})
    assert status == 200 and document["study"]["name"] == "Keep me 2"
    assert call(app, "GET", "/api/home")[1]["studies"][0]["archived"] is False

    status, gone = call(app, "DELETE", "/api/studies/keep-me")
    assert status == 200 and gone["trashed"].startswith("keep-me~"), gone
    assert call(app, "GET", "/api/studies/keep-me")[0] == 404
    status, home = call(app, "GET", "/api/home")
    assert [s["slug"] for s in home["studies"]] == ["keep-me-too"]
    (entry,) = home["trash"]
    assert entry["entry"] == gone["trashed"] and entry["name"] == "Keep me" and entry["deleted_at"]
    assert call(app, "DELETE", "/api/studies/keep-me")[0] == 404

    status, home = call(app, "POST", f"/api/trash/{entry['entry']}/restore")
    assert status == 200 and sorted(s["slug"] for s in home["studies"]) == ["keep-me", "keep-me-too"] and home["trash"] == []
    assert call(app, "GET", "/api/studies/keep-me")[0] == 200
    assert call(app, "POST", f"/api/trash/{entry['entry']}/restore")[0] == 404
    assert call(app, "POST", "/api/trash/..%2Fkeep-me/restore")[0] == 404


def test_a_study_zip_round_trip_refuses_zip_slip(app):
    import io
    import zipfile

    call(app, "POST", "/api/studies", {"harness": "demo-tour", "template": "tune", "name": "Pack"})
    call(app, "POST", "/api/studies/pack/application", {"path": str(SOLVER)})
    call(app, "POST", "/api/studies/pack/samples")
    status, data = call(app, "GET", "/api/studies/pack/download/study")
    assert status == 200 and isinstance(data, bytes)
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert "study.yaml" in names and "cases/town-40.json" in names and "harness/harness.yaml" in names

    status, document = call(app, "POST", "/api/studies?name=pack.zip", data, headers={"Content-Type": "application/zip"})
    assert status == 200 and document["slug"] == "pack-2" and document["study"]["name"] == "Pack", document
    assert len(document["cases"]) == 4

    def zipped(members: dict[str, bytes]) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, content in members.items():
                archive.writestr(name, content)
        return buffer.getvalue()

    status, body = call(app, "POST", "/api/studies?name=evil.zip", zipped({"study.yaml": b"x", "../evil.txt": b"x"}),
                        headers={"Content-Type": "application/zip"})
    assert status == 400 and "outside" in body["error"], body
    status, body = call(app, "POST", "/api/studies?name=no.zip", zipped({"harness/harness.yaml": b"x"}),
                        headers={"Content-Type": "application/zip"})
    assert status == 400 and "study.yaml" in body["error"], body
    assert [s["slug"] for s in call(app, "GET", "/api/home")[1]["studies"]] == ["pack-2", "pack"]


def test_a_removed_case_can_be_restored(app):
    call(app, "POST", "/api/studies", {"harness": "demo-tour", "template": "tune", "name": "Cases"})
    call(app, "POST", "/api/studies/cases/application", {"path": str(SOLVER)})
    call(app, "POST", "/api/studies/cases/samples")
    status, document = call(app, "DELETE", "/api/studies/cases/cases/town-40.json")
    assert status == 200 and "town-40.json" not in [c["name"] for c in document["cases"]]
    status, document = call(app, "POST", "/api/studies/cases/cases/town-40.json/restore")
    assert status == 200 and "town-40.json" in [c["name"] for c in document["cases"]]
    assert "cases/town-40.json" in document["study"]["cases"]["training"]
    restored = next(c for c in document["cases"] if c["name"] == "town-40.json")
    assert restored["inspected"]["ok"] is True
    assert call(app, "POST", "/api/studies/cases/cases/town-40.json/restore")[0] == 404
