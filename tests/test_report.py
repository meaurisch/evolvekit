"""`study verdict` and `study report`: written from the verdict document."""

from __future__ import annotations

import re

import pytest
from test_verdict import RUN, _run_of

from evolvekit.app import jobs
from evolvekit.app.report import render_report
from evolvekit.app.verdict import build_verdict
from evolvekit.cli import main
from evolvekit.status import build_status

HEADINGS = ["## Verdict", "## Held-back cases", "## What changed", "## How it ran", "## Files"]


@pytest.fixture
def use_run(tmp_path):
    return _run_of(tmp_path, "use")


@pytest.fixture
def keep_run(tmp_path):
    return _run_of(tmp_path, "keep")


def _home(root):
    return str(root.parent.parent)


def _text(root):
    return render_report(build_verdict(root, RUN), jobs.results(root, RUN, build_status))


def test_report_markdown_sections(use_run):
    root, _ = use_run
    text = _text(root)
    positions = [text.index(f"\n{heading}\n") for heading in HEADINGS]
    assert positions == sorted(positions)
    assert text.startswith("# ")


def test_report_uses_the_verdict_line_verbatim(use_run):
    root, _ = use_run
    v = build_verdict(root, RUN)
    text = _text(root)
    assert v["line"] in text and v["advice"] in text


def test_report_never_says_better_without_use(keep_run):
    root, _ = keep_run
    v = build_verdict(root, RUN)
    assert "better" in v["line"]
    assert _text(root).count("better") == v["line"].count("better") + v["advice"].count("better")


def test_report_numbers_use_the_verdict_wording(use_run):
    root, _ = use_run
    v = build_verdict(root, RUN)
    low, high = v["ci95_pct"]
    text = _text(root)
    assert f"between {low:.1f} % and {high:.1f} % lower" in text
    assert not re.search(r"[+]\s?\d", text)
    assert "better" not in text.replace(v["line"], "").replace(v["advice"], "")


def test_verdict_no_run(tmp_path, capsys):
    from test_app_results import study_with_runs

    root, _ = study_with_runs(tmp_path)
    assert main(["study", "verdict", root.name, "--home", _home(root)]) == 1
    assert "no run yet" in capsys.readouterr().err


def test_verdict_for_people(use_run, capsys):
    root, _ = use_run
    v = build_verdict(root, RUN)
    assert main(["study", "verdict", root.name, "--home", _home(root)]) == 0
    assert capsys.readouterr().out == v["line"] + "\n" + v["advice"] + "\n"


def test_report_markdown_cli(use_run, capsys):
    root, _ = use_run
    assert main(["study", "report", root.name, "--markdown", "--home", _home(root)]) == 0
    assert capsys.readouterr().out == _text(root)
    assert main(["study", "report", root.name, "--home", _home(root)]) == 0
    import json

    assert json.loads(capsys.readouterr().out) == build_verdict(root, RUN)


def test_numbers_are_written_as_people_write_them():
    from evolvekit.app.report import _number

    assert _number(1248920.0) == "1,248,920" and _number(12.345) == "12.35" and _number(7.0) == "7"
    assert _number(0.00001234) == "0.000012" and _number(None) == "-"
    verdict = {"study": "s", "line": "l", "advice": "a", "measure": {"direction": "lower", "label": "cost"},
               "by_case": [{"name": "c", "start": 1248920.0, "best": 12345.0, "change_pct": 1.3, "outcome": "better"}]}
    text = render_report(verdict)
    assert "1,248,920" in text and "12,345" in text and "e+" not in text


@pytest.mark.parametrize("decision", ["use", "keep", "worse", "none", "single", "pending", "stopped", "skipped"])
def test_every_decision_renders(decision):
    verdict = {"schema": 1, "study": "s", "run": "r", "phase": "done", "decision": decision, "line": "The line.",
               "advice": "", "measure": {"kpi": "k", "label": "cost", "unit": "", "direction": "higher"},
               "held_back": {"cases": 0, "runs_per_case": None}, "ci95_pct": None, "mean_pct": None,
               "learned_from": {"cases": 3, "pct": None}, "rule": "The rule.", "by_case": [], "changed": [],
               "files": [], "reason": "the line", "elapsed_s": None, "rounds": None, "combinations": None}
    text = render_report(verdict)
    positions = [text.index(f"\n{heading}\n") for heading in HEADINGS]
    assert positions == sorted(positions)
    assert "- The line." not in text, "the reason is not repeated when the line says it"


def test_report_on_a_run_with_no_final_check(tmp_path, capsys):
    from test_app_results import finished_run, study_with_runs

    root, _ = study_with_runs(tmp_path)
    finished_run(root / "runs" / RUN, check=False)
    assert main(["study", "report", root.name, "--markdown", "--home", _home(root)]) == 0
    out = capsys.readouterr().out
    assert all(f"\n{heading}\n" in out for heading in HEADINGS)
