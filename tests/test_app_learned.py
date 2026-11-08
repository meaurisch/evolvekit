"""The "What we learned" page: the harness route's `learned` and the page's words (`pages/harness.js`)."""

from __future__ import annotations

import json
import re

from test_app_page import STATIC, _node, _page_function, _visible_strings
from test_app_server import call
from test_memory import finished

from evolvekit.app.server import AppServer

HARNESS_PAGE = (STATIC / "js" / "pages" / "harness.js").read_text(encoding="utf-8")


def _render_text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", html)).strip()


def test_api_harness_learned(tmp_path):
    server = AppServer(tmp_path / "my home", port=0)
    server.start()
    try:
        finished(server.home, {}, "Spring", decision="use", best={"neighbours": 12}, finished_at="2026-10-01")
        status, got = call(server, "GET", "/api/harnesses/demo-tour")
    finally:
        server.stop()
    assert status == 200
    assert got["learned"]["studies"][0]["name"] == "Spring" and set(got["learned"]) == {"studies", "settings", "notes"}


LEARNED = {"title": "Demo tour", "learned": {"studies": [{"slug": "spring", "name": "Spring", "run": "r1",
    "finished_at": "2026-10-01T00:12:00+00:00", "decision": "use", "line": "Probably better: 3.1 % lower tour length …", "kind": "settings"}],
    "settings": {"neighbours": {"label": "Candidate moves per stop", "tuned_in": 2, "importance": 0.42, "importance_n": 2,
        "winners": [{"value": 12, "study": "spring", "finished_at": "2026-10-01T00:12:00+00:00"}], "direction": "up"}},
    "notes": ["old_knob (in Spring) is not a setting of Demo tour 1.1.0 any more: left out"]}}


def _view(data: dict) -> str:
    parts = [_page_function(HARNESS_PAGE, n) for n in ("dayOf", "studiesList", "importanceCell", "winnersCell", "learnedView")]
    out = _node(*parts,
                f"process.stdout.write(JSON.stringify(String(learnedView({json.dumps(data)}))));")
    return _render_text(out)


def test_harness_page_shows_learned():
    text = _view(LEARNED)
    for words in ("What we learned · Demo tour", "From 1 finished studies on this application.",
                  "A winning value comes only from a study whose final check said use.",
                  "Candidate moves per stop", "0.42 · from 2 studies", "12 in Spring (1 Oct)", "up", "Left out",
                  "finished 1 Oct", "Probably better: 3.1 % lower tour length", "How strongly it went with the measure",
                  "Winners moved it", "old_knob (in Spring)"):
        assert words in text, words


def test_harness_page_says_what_is_missing():
    empty = _view({"title": "Demo tour", "learned": {"studies": [], "settings": {}, "notes": []}})
    assert "No study on this application has finished yet." in empty and "Left out" not in empty
    lone = json.loads(json.dumps(LEARNED))
    setting = lone["learned"]["settings"]["neighbours"]
    setting.update(importance=None, importance_n=0, winners=[], direction=None)
    text = _view(lone)
    assert "not enough tries to tell" in text and "none yet" in text and "—" in text
    setting["direction"] = "mixed"
    assert "both ways" in _view(lone)


def test_harness_page_never_says_better():
    strings = _visible_strings(HARNESS_PAGE)
    assert not [s for s in strings if "better" in s.lower()], "only a verdict's own line says it, from the data"


def test_the_harness_route_and_its_script_ship():
    app_js = (STATIC / "js" / "app.js").read_text(encoding="utf-8")
    assert "harness: \"harness\"" in app_js
    assert "static/js/pages/harness.js" in (STATIC / "app.html").read_text(encoding="utf-8")
    home_js = (STATIC / "js" / "pages" / "home.js").read_text(encoding="utf-8")
    assert home_js.count("What we learned") >= 2, "the Applications drawer and the links row"
