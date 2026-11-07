"""The app and a code study (design §5.2, §5.8): Set up shows what may change
in code, and the drawer that says where the source is posts to its route."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from test_app_server import app, call  # noqa: F401 - the fixture and the request helper

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "evolvekit" / "app" / "static"
SOURCE = (ROOT / "examples" / "cli-solver").resolve()


def test_setup_page_shows_code_summary():
    setup = (STATIC / "js" / "pages" / "setup.js").read_text(encoding="utf-8")
    body = setup[setup.index("function codeSummary"):setup.index("function", setup.index("function codeSummary") + 10)]
    assert 'kicker: "What may change: code"' in body and "vary().code" in body and 'act: "code"' in body
    assert "var rows = [tuneSummary(), dataSummary(), codeSummary(), goalSummary(), timeSummary()]" in setup
    drawer = setup[setup.index("function codeDrawer"):]
    assert 'EK.studyPath(st.slug, "/code")' in drawer and "code_proposal" in drawer and "brief" in drawer
    assert "code: function () { codeDrawer(main); }" in setup


def test_setup_page_keeps_the_code_when_it_saves_the_settings():
    """The tune and data drawers write `vary` whole: a code study's `vary.code` goes with it."""
    setup = (STATIC / "js" / "pages" / "setup.js").read_text(encoding="utf-8")
    assert "vary: { settings:" not in setup and "varyWith(" in setup


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_the_code_route_sets_the_source(app):  # noqa: F811 - the fixture
    status, document = call(app, "POST", "/api/studies", {"harness": "demo-tour", "template": "faster", "name": "Faster"})
    assert status == 200 and document["code_proposal"] == str(SOURCE)
    assert any(p["key"] == "vary.code.repo" and p["step"] == 4 for p in document["problems"])
    status, harness = call(app, "GET", "/api/studies/faster/harness")
    assert harness["code"]["open"] == ["solver.py"] and harness["code"]["label"] == "The solver's source"
    status, answer = call(app, "POST", "/api/studies/faster/code", {"repo": str(SOURCE), "from": "HEAD", "brief": "Shorter tours."})
    assert status == 200 and answer["code"]["ok"] is True
    assert answer["study"]["study"]["vary"]["code"] == {"repo": str(SOURCE), "from": "HEAD", "brief": "Shorter tours."}
    assert not any(p["key"].startswith("vary.code") for p in answer["study"]["problems"])
    status, answer = call(app, "POST", "/api/studies/faster/code", {"repo": "cli-solver"})
    assert status == 400 and "give the full path to the source" in answer["error"]
