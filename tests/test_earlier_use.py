"""A later run must not hide an earlier `use` (persona round 3, E4).

After the bake-off said keep, Home and `study status` read "not proven · Keep
today's code" while run 1's AI change said use and had been pushed. When the
latest run's decision is not `use` and an earlier run of the same study
decided `use`, Home's card, `study status` and the Study page header add one
line, "An earlier run (<id>) says use: <its verdict line>"; and a run `study
pr` made a branch for says "Branch <name> pushed to <remote>" (or "made, not
pushed") on its Study page and in `study status`."""

from __future__ import annotations

import json
from pathlib import Path

from test_app_page import STUDY_PAGE, _node, _page_function
from test_app_results import finished_run, study_with_runs

from evolvekit.app import jobs
from evolvekit.app.verdict import build_verdict
from evolvekit.cli import main
from evolvekit.harness.study import load_study

FIRST, SECOND = "20260101-000000", "20260102-000000"


def _two_runs(tmp_path: Path, *, second: str = "keep"):
    root, home = study_with_runs(tmp_path)
    finished_run(root / "runs" / FIRST, decision="use")
    finished_run(root / "runs" / SECOND, started="2026-01-02T00:00:00+00:00", decision=second, mean=0.4)
    return root, home


def _record(run_dir: Path, *, pushed: bool) -> None:
    (run_dir / jobs.PR_RECORD).write_text(json.dumps({
        "branch": "evolvekit/tours-" + run_dir.name, "commit": "3beea9d" + "0" * 33,
        "remote": "origin" if pushed else None, "pushed": pushed, "url": None}), encoding="utf-8")


def test_a_later_keep_names_the_earlier_use(tmp_path):
    root, _ = _two_runs(tmp_path)
    line = build_verdict(root, FIRST)["line"]
    state = jobs.state_line(root, load_study(root))
    assert state["decision"] == "keep"
    assert state["earlier"] == {"run": FIRST, "line": line, "says": f"An earlier run ({FIRST}) says use: {line}"}
    assert line.startswith("Probably better"), "the verdict's own line, quoted: the only claim"


def test_no_earlier_line_when_the_latest_says_use_or_none_did(tmp_path):
    root, _ = _two_runs(tmp_path, second="use")
    assert "earlier" not in jobs.state_line(root, load_study(root))
    other, _ = study_with_runs(tmp_path / "other")
    finished_run(other / "runs" / FIRST, decision="keep", mean=0.4)
    finished_run(other / "runs" / SECOND, started="2026-01-02T00:00:00+00:00", decision="keep", mean=0.4)
    assert "earlier" not in jobs.state_line(other, load_study(other))


def test_study_status_says_the_earlier_use_and_its_branch(tmp_path, capsys):
    root, home = _two_runs(tmp_path)
    _record(root / "runs" / FIRST, pushed=True)
    assert main(["study", "status", root.name, "--home", str(home.root)]) == 0
    out = capsys.readouterr().out.splitlines()
    line = build_verdict(root, FIRST)["line"]
    assert f"An earlier run ({FIRST}) says use: {line}" in out
    assert f"Branch evolvekit/tours-{FIRST} pushed to origin" in out
    assert out.index(f"Branch evolvekit/tours-{FIRST} pushed to origin") == \
        out.index(f"An earlier run ({FIRST}) says use: {line}") + 1, "the branch under the run it belongs to"


def test_a_runs_own_branch_is_said_with_it(tmp_path, capsys):
    root, home = _two_runs(tmp_path, second="use")
    _record(root / "runs" / SECOND, pushed=False)
    assert main(["study", "status", root.name, "--home", str(home.root)]) == 0
    assert f"Branch evolvekit/tours-{SECOND} made, not pushed" in capsys.readouterr().out.splitlines()
    listed = {r["id"]: r for r in jobs.runs(root)}
    assert listed[SECOND]["branch"]["line"] == f"Branch evolvekit/tours-{SECOND} made, not pushed"
    assert "branch" not in listed[FIRST]
    _record(root / "runs" / FIRST, pushed=True)
    assert jobs.runs(root)[0]["branch"]["line"] == f"Branch evolvekit/tours-{FIRST} pushed to origin", \
        "a record written after the card was kept is read"


def test_home_and_the_study_page_say_the_earlier_use(tmp_path):
    root, home = _two_runs(tmp_path)
    line = build_verdict(root, FIRST)["line"]
    says = f"An earlier run ({FIRST}) says use: {line}"
    card = jobs.state_line(root, load_study(root))
    out = _node(f"""
      EK.studyHref = EK.studyHref || function (slug) {{ return "#/study/" + slug; }};
      EK.pepper = EK.pepper || function () {{ return ""; }};  // the butterfly's drawing is another script's
      var c = EK.cardOf({json.dumps({"slug": "tours", "name": "Tours", **card})});
      process.stdout.write(JSON.stringify({{note: c.note, html: String(EK.studyCard(c))}}));""")
    assert out["note"] == says and "card-note" in out["html"] and "An earlier run" in out["html"]
    runs = [{"id": FIRST, "branch": {"line": "Branch b pushed to origin"}}, {"id": SECOND}]
    lines = _node(_page_function(STUDY_PAGE, "headLines"), f"""
      var earlier = {json.dumps(card["earlier"])}, runs = {json.dumps(runs)};
      process.stdout.write(JSON.stringify({{latest: headLines(runs, "{SECOND}", earlier),
        first: headLines(runs, "{FIRST}", earlier), none: headLines(runs, "{SECOND}", null)}}));""")
    assert lines["latest"] == [says], "the latest run's page names the earlier use"
    assert lines["first"] == ["Branch b pushed to origin"], "that run's own page says its branch, not itself"
    assert lines["none"] == []
    assert "headLines(" in _page_function(STUDY_PAGE, "header")
