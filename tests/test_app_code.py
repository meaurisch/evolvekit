"""The app and a code study (design §5.2, §5.8): Set up shows what may change
in code, and the drawer that says where the source is posts to its route."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from test_app_results import finished_run, study_with_runs
from test_app_server import app, call  # noqa: F401 - the fixture and the request helper
from tree_helpers import SOLVER, diff_text

from evolvekit.app import tries
from evolvekit.app.server import AppServer

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "evolvekit" / "app" / "static"
SOURCE = (ROOT / "examples" / "cli-solver").resolve()
RUN = "20260101-000000"
T0 = "2026-01-01T00:00:00+00:00"


def test_setup_page_shows_code_summary():
    setup = (STATIC / "js" / "pages" / "setup.js").read_text(encoding="utf-8")
    body = setup[setup.index("function codeSummary"):setup.index("function", setup.index("function codeSummary") + 10)]
    assert 'kicker: "What may change: code"' in body and "vary().code" in body and 'act: "code"' in body
    assert "var rows = [tuneSummary(), dataSummary(), codeSummary(), goalSummary(), timeSummary(), whenSummary()]" in setup
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


# -- the Tries view of a code study shows each combination's change (design §5.8)


def _at(minute: int) -> str:
    return f"2026-01-01T00:{minute:02d}:00+00:00"


def finished_code_run(tmp_path: Path, odd: str = "") -> tuple[Path, str, Path]:
    """A finished code run written by hand into a study's `runs/<RUN>/`: the
    starting point (no change), `c1` (one line of `app dir/solver.py`) and `c2`
    (that and a new file holding `odd` as it is). Returns the app's home
    folder, the study's slug and the run folder."""
    root, home = study_with_runs(tmp_path)
    run_dir = root / "runs" / RUN
    finished_run(run_dir)  # job.json and the final check of c1
    (run_dir / "evolvekit.yaml").write_text(yaml.safe_dump({"problem": {"tree": {
        "repo": str(tmp_path / "my repo"), "base": "0" * 40, "open": ["app dir/solver.py", "app dir/notes.txt"]}}}),
        encoding="utf-8")
    one = diff_text("app dir/solver.py", SOLVER, SOLVER.replace("VALUE = 5", "VALUE = 3"))
    two = one + diff_text("app dir/notes.txt", "", f"<b>bold</b>\n{odd}\n")
    events = [{"type": "run_started", "ts": T0, "objective": "tour_length", "direction": "minimize", "parameters": []},
              {"type": "generation_started", "ts": T0, "generation": 0}, {"type": "stage_started", "ts": T0, "stage": "full"}]
    for minute, (cid, value) in enumerate([("seed", 100.0), ("c1", 96.0), ("c2", 98.0)], start=1):
        events += [{"type": "eval_started", "ts": _at(minute), "candidate_id": cid, "stage": "full", "instance": "a", "seed": 0},
                   {"type": "eval_finished", "ts": _at(minute), "candidate_id": cid, "stage": "full", "instance": "a", "seed": 0,
                    "ok": True, "kpis": {"tour_length": value}}]
    events.append({"type": "run_finished", "ts": _at(9)})
    (run_dir / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    rows = [{"id": "seed", "generation": 0, "operator": "human-seed", "parent_id": None, "params": {}, "block": ""},
            {"id": "c1", "generation": 1, "operator": "mutate", "parent_id": "seed", "params": {}, "block": one},
            {"id": "c2", "generation": 1, "operator": "mutate", "parent_id": "c1", "params": {}, "block": two}]
    (run_dir / "runs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return home.root, root.name, run_dir


def _served(home: Path) -> AppServer:
    server = AppServer(home, port=0)
    server.start()
    return server


def test_tries_api_carries_diff(tmp_path):
    home, slug, run_dir = finished_code_run(tmp_path)
    combos = {c["id"]: c for c in tries.digest(run_dir)["combinations"]}
    assert combos["c1"]["diff_stat"] == {"added": 1, "removed": 1, "files": ["app dir/solver.py"]}
    assert combos["c2"]["diff_stat"] == {"added": 3, "removed": 1, "files": ["app dir/notes.txt", "app dir/solver.py"]}
    assert combos["seed"]["diff_stat"] == {"added": 0, "removed": 0, "files": []}, "the starting point changes nothing"
    server = _served(home)
    try:
        status, body = call(server, "GET", f"/api/studies/{slug}/tries/c1?run={RUN}")
        assert status == 200 and "+VALUE = 3" in body["diff"] and "-VALUE = 5" in body["diff"]
        status, body = call(server, "GET", f"/api/studies/{slug}/tries/seed?run={RUN}")
        assert status == 200 and body["diff"] == ""
    finally:
        server.stop()
    plain = tmp_path / "plain" / "runs" / RUN
    shutil.copytree(run_dir, plain)
    (plain / "evolvekit.yaml").write_text("problem:\n  skeleton: solver.py\n", encoding="utf-8")
    assert all(c["diff_stat"] is None for c in tries.digest(plain)["combinations"]), "a settings run has no change to count"
    root, _ = study_with_runs(tmp_path / "s")
    finished_run(root / "runs" / RUN)
    assert all(c["diff_stat"] is None for c in tries.digest(root / "runs" / RUN)["combinations"])


def test_tries_diff_with_odd_text_still_serves(tmp_path):
    """A diff may carry U+FFFD or a lone surrogate (a file that was not UTF-8): the count and the API still work."""
    home, slug, run_dir = finished_code_run(tmp_path, odd="caf� \udc80")
    combos = {c["id"]: c for c in tries.digest(run_dir)["combinations"]}
    assert combos["c2"]["diff_stat"]["added"] == 3
    server = _served(home)
    try:
        status, body = call(server, "GET", f"/api/studies/{slug}/tries/c2?run={RUN}")
        assert status == 200 and "+caf� \udc80" in body["diff"]
        status, body = call(server, "GET", f"/api/studies/{slug}/tries?run={RUN}")
        assert status == 200 and {c["id"]: c for c in body["combinations"]}["c2"]["diff_stat"]["added"] == 3
    finally:
        server.stop()


def test_tries_drawer_renders_diff():
    js = (STATIC / "js" / "pages" / "study.js").read_text(encoding="utf-8")
    view = js[js.index("function diffView"):js.index("function", js.index("function diffView") + 10)]
    assert "diff-add" in view and "diff-del" in view and "diff-hunk" in view and "<pre" in view
    assert "hljs" not in js and "highlight" not in view, "no highlighting library: added and removed lines coloured"
    assert "diffWords(c.diff_stat)" in js[js.index("function cardLines"):js.index("function comboDrawer")]
    assert "d.diff ? diffView(d.diff)" in js[js.index("function comboDrawer"):js.index("function runsDrawer")]
    css = (STATIC / "css" / "app.css").read_text(encoding="utf-8")
    assert ".diff-add" in css and "var(--good-tint)" in css[css.index(".diff-add"):][:200]
    assert ".diff-del" in css and "var(--critical-tint)" in css[css.index(".diff-del"):][:200]
    rule = css[css.index(".diff {"):css.index("}", css.index(".diff {"))]
    assert "overflow" in rule and "var(--type-data)" in rule, "a long diff scrolls in its own box, in the data type"


def _function(js: str, name: str) -> str:
    """The source of one of the page's two-space-indented functions."""
    start = js.index(f"  function {name}(")
    return js[start:js.index("\n  }\n", start) + 4]


def test_diff_view_escapes_marks_and_counts():
    """`diffWords` and `diffView` themselves, run with the page's own `EK.html`."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not on PATH")
    js = (STATIC / "js" / "pages" / "study.js").read_text(encoding="utf-8")
    core = (STATIC / "js" / "core.js").read_text(encoding="utf-8")
    diff = ("diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1,3 +1,3 @@\n keep\n"
            "--- an old comment\n+<script>alert(1)</script> caf�\n+++ more\n")
    script = "\n".join([
        "var window = {}, location = {href: 'http://127.0.0.1/'};", core, "var EK = window.EK, html = EK.html;",
        _function(js, "diffWords"), _function(js, "diffView"),
        "var out = {view: String(diffView(" + json.dumps(diff) + " + '\\ud800 lone')),",
        " one: diffWords({added: 3, removed: 1, files: ['app dir/solver.py']}),",
        " two: diffWords({added: 3, removed: 1, files: ['a.py', 'b.py']}),",
        " none: diffWords({added: 0, removed: 0, files: []})};",
        "process.stdout.write(JSON.stringify(out));",
    ])
    done = subprocess.run([node, "-"], input=script, capture_output=True, encoding="utf-8", check=False)
    assert done.returncode == 0, done.stderr
    out = json.loads(done.stdout)
    view = out["view"]
    assert view.startswith("<pre") and "<script>" not in view and "&lt;script&gt;" in view
    assert '<span class="diff-add">+&lt;script&gt;alert(1)&lt;/script&gt; caf�</span>' in view
    assert '<span class="diff-del">--- an old comment</span>' in view, "inside a hunk a removed '-- ' line is a removed line"
    assert '<span class="diff-add">+++ more</span>' in view
    assert '<span class="diff-hunk">@@ -1,3 +1,3 @@</span>' in view
    assert "<span>--- a/x.py</span>" in view and "<span>+++ b/x.py</span>" in view and "<span> keep</span>" in view
    assert "\ud800 lone" in view, "a lone surrogate is shown as it is, not thrown on"
    assert out["one"] == "+3 −1 lines in solver.py" and out["two"] == "+3 −1 lines in 2 files"
    assert out["none"] == "No change: today’s code"
