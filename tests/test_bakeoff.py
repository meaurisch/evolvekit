"""The bake-off (design §5.5): `study bakeoff SLUG --ref A --ref B`, a run with
no search whose candidates are the given branches, each checked against the
starting point on the held-back cases; the verdict document's `candidates`;
the app's "Compare branches".

The repositories are temporary copies of `examples/cli-solver`: no test makes
a branch or a ref in the evolvekit checkout."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from test_app_results import finished_run, study_with_runs
from test_app_server import call
from tree_helpers import git, git_init_commit

from evolvekit.app import AppError, jobs
from evolvekit.app.report import render_report
from evolvekit.app.server import AppServer
from evolvekit.app.verdict import build_verdict
from evolvekit.cli import main
from evolvekit.harness.execute import run_bakeoff
from evolvekit.harness.library import create_study
from evolvekit.harness.manifest import load_harness
from evolvekit.harness.study import load_study, save_study
from evolvekit.ledger import read_jsonl

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "harnesses" / "demo-tour"
STATIC = ROOT / "evolvekit" / "app" / "static"
RUN = "20260101-000000"


def _solver_repo(tmp_path) -> Path:
    """examples/cli-solver as a repository of its own, with two branches: one that changes nothing, one that
    restarts four times sooner."""
    top = tmp_path / "solver repo"
    shutil.copytree(ROOT / "examples" / "cli-solver", top, ignore=shutil.ignore_patterns("fresh", "__pycache__"))
    git_init_commit(top)
    git(top, "switch", "-q", "-c", "same")
    git(top, "commit", "-q", "--allow-empty", "-m", "same")
    git(top, "switch", "-q", "main")
    git(top, "switch", "-q", "-c", "restart")
    path = top / "solver.py"
    text = path.read_text(encoding="utf-8")
    assert "if stalled >= a.restart_after:" in text
    path.write_text(text.replace("if stalled >= a.restart_after:", "if stalled >= a.restart_after // 4:"),
                    encoding="utf-8", newline="\n")
    git(top, "commit", "-q", "-am", "restart sooner")
    git(top, "switch", "-q", "main")
    return top


def _code_study(tmp_path, top) -> Path:
    folder = tmp_path / "study"
    create_study(folder, DEMO, "Branches", "faster")
    for sample in sorted((DEMO / "samples").glob("*.json")):
        shutil.copy(sample, folder / "cases" / sample.name)
    study = load_study(folder)
    study.code.repo, study.application_path = str(top), str(top / "solver.py")
    study.training, study.test = ["cases/north-south-30.json"], ["cases/region-60.json", "cases/town-40.json"]
    study.limits.time_per_case_s = 0.5
    save_study(study, folder)
    return folder


@pytest.mark.slow
def test_bakeoff_two_refs_on_demo_tour(tmp_path):
    top = _solver_repo(tmp_path)
    folder = _code_study(tmp_path, top)
    (folder / "runs" / "b1").mkdir(parents=True)
    (folder / "runs" / "b1" / "bakeoff.json").write_text(json.dumps({"refs": ["same", "restart"]}), encoding="utf-8")
    job = run_bakeoff(folder, "b1", log=lambda _m: None)
    assert job["phase"] == "done" and job["search"]["generations"] == 0
    assert job["search"]["stop_reason"] == "branches compared: no search"
    assert [b["ref"] for b in job["bakeoff"]] == ["same", "restart"]
    rows = list(read_jsonl(folder / "runs" / "b1" / "runs.jsonl"))
    assert rows[0]["operator"] == "human-seed" and rows[0]["block"] == "" and rows[1]["block"] == ""
    assert "restart_after // 4" in rows[2]["block"] and rows[2]["commit"] == git(top, "rev-parse", "restart")
    assert set(job["final"]["decisions"]) == {b["id"] for b in job["bakeoff"]}
    verdict = build_verdict(folder, "b1")
    assert [c["ref"] for c in verdict["candidates"]] == ["same", "restart"]
    assert all(c["decision"] in ("use", "keep", "worse") and c["line"] for c in verdict["candidates"])
    ref_of = {b["id"]: b["ref"] for b in job["bakeoff"]}
    chosen = ref_of[job["final"]["candidate"]]
    own = next(c for c in verdict["candidates"] if c["ref"] == chosen)
    if own["decision"] == "use":  # decision 32: the chosen branch's line, said as one of N
        assert verdict["decision"] == "use" and verdict["line"] == f"Of 2 branches, {chosen}: {own['line'][0].lower()}{own['line'][1:]}"
    else:
        assert verdict["decision"] == "keep"
        assert verdict["line"] == "None of the 2 branches is shown to beat the starting point."
    assert verdict["advice"].startswith(("Use", "Keep today's code"))
    report = render_report(verdict)
    assert "2 branches were compared; the more are compared, the likelier one looks ahead by chance." in report
    assert not any((folder / "runs" / "b1" / "confirm" / "final" / "work" / "trees").glob("*"))
    assert git(top, "status", "--porcelain") == "" and "trees" not in git(top, "worktree", "list")


def test_bakeoff_refuses_on_settings_study(tmp_path, capsys):
    home = str(tmp_path)
    main(["study", "new", "Tune", "--harness", "demo-tour", "--template", "tune", "--home", home])
    assert main(["study", "bakeoff", "tune", "--ref", "main", "--home", home]) == 2
    assert "study bakeoff compares branches of a code study; this study tunes settings" in capsys.readouterr().err


def test_bakeoff_refuses_unknown_and_outside_refs(tmp_path):
    top = _solver_repo(tmp_path)
    folder = _code_study(tmp_path, top)
    git(top, "switch", "-q", "-c", "readme")
    (top / "README.md").write_text("x\n")
    git(top, "commit", "-q", "-am", "r")
    git(top, "switch", "-q", "main")
    study, harness = load_study(folder), load_harness(folder / "harness")
    with pytest.raises(AppError, match="nope: no such branch"):
        jobs.check_refs(folder, study, harness, ["nope"])
    with pytest.raises(AppError, match=r"readme changes files outside the open files: README\.md; widen vary\.code\.open"):
        jobs.check_refs(folder, study, harness, ["readme"])
    with pytest.raises(AppError, match="same is given twice"):
        jobs.check_refs(folder, study, harness, ["same", "same"])
    with pytest.raises(AppError, match="refs: name at least one branch"):
        jobs.check_refs(folder, study, harness, [])
    checked = jobs.check_refs(folder, study, harness, ["same", "restart"])
    assert [r["ref"] for r in checked] == ["same", "restart"]
    assert checked[1] == {"ref": "restart", "commit": git(top, "rev-parse", "restart"), "files": ["solver.py"]}
    assert checked[0]["files"] == []
    # Decision 34: the branch's own change, from its merge-base; one that no longer applies is refused.
    git(top, "switch", "-q", "-c", "clash", "restart~1")
    path = top / "solver.py"
    path.write_text(path.read_text(encoding="utf-8").replace("if stalled >= a.restart_after:",
                                                            "if stalled >= a.restart_after // 2:"),
                    encoding="utf-8", newline="\n")
    git(top, "commit", "-q", "-am", "restart a little sooner")
    git(top, "switch", "-q", "main")
    git(top, "merge", "-q", "--ff-only", "restart")
    with pytest.raises(AppError, match="clash: does not apply on the base; rebase clash onto it"):
        jobs.check_refs(folder, study, harness, ["clash"])
    # No held-back case: nothing to compare on.
    study.test = []
    with pytest.raises(AppError, match="cases.test: hold back at least one case; branches are compared on held-back cases"):
        jobs.check_refs(folder, study, harness, ["restart"])
    assert git(top, "status", "--porcelain") == ""


def test_jobs_start_writes_the_refs_and_launches_with_bakeoff(tmp_path, monkeypatch):
    from evolvekit.app.store import Home

    top = _solver_repo(tmp_path)
    home = Home(tmp_path / "home")
    slug = home.create_study("demo-tour", "faster", "Branches")
    root = home.study_root(slug)
    for sample in sorted((DEMO / "samples").glob("*.json")):
        shutil.copy(sample, root / "cases" / sample.name)
    study = load_study(root)
    study.code.repo, study.application_path = str(top), str(top / "solver.py")
    study.training, study.test = ["cases/north-south-30.json"], ["cases/region-60.json"]
    save_study(study, root)
    launched = []
    monkeypatch.setattr(jobs, "_launch", lambda h, r, run_dir, extra, **kw: launched.append((run_dir.name, extra, kw)) or 1)
    with pytest.raises(AppError, match="nope: no such branch"):
        jobs.start(home, root, bakeoff=["nope"])
    assert not (root / "runs").exists() or not any((root / "runs").iterdir()), "a bad ref makes no run"
    answer = jobs.start(home, root, bakeoff=["restart"])
    run_dir = root / "runs" / answer["run"]
    assert json.loads((run_dir / "bakeoff.json").read_text(encoding="utf-8")) == {"refs": ["restart"]}
    assert launched == [(answer["run"], ["--bakeoff"], {"with_keys": False})]
    assert jobs._kind_of(study) == "code" and jobs._what(study) == "code change"


def _as_bakeoff(run_dir: Path, decisions: dict | None = None, *, only: str | None = None) -> None:
    """A finished run made into a bake-off by hand: c1 is the branch `restart` (use), a copy c2 the branch `same`,
    0.5 % worse (keep), given first. `decisions` replaces the branches' own; `only="c1"` keeps one branch."""
    job = json.loads((run_dir / "job.json").read_text(encoding="utf-8"))
    job["bakeoff"] = [{"ref": "same", "id": "c2", "commit": "b" * 40}, {"ref": "restart", "id": "c1", "commit": "a" * 40}]
    if only:
        job["bakeoff"] = [e for e in job["bakeoff"] if e["id"] == only]
    job["search"] = {"stop_reason": "branches compared: no search", "generations": 0, "best_id": "c1", "aborted": False}
    if job.get("final"):
        job["final"]["decisions"] = decisions or {"c1": "use", "c2": "keep"}
    (run_dir / "job.json").write_text(json.dumps(job), encoding="utf-8")
    path = run_dir / "confirm" / "final" / "comparison.json"
    if not path.is_file():
        return
    comparison = json.loads(path.read_text(encoding="utf-8"))
    comparison["per_candidate"]["c2"] = {"summary": {"n": 2, "mean": -0.5, "ci95": [-1.7, 0.7], "chance": 0.3, "worst": -1.7}}
    path.write_text(json.dumps(comparison), encoding="utf-8")
    rows = list(read_jsonl(run_dir / "runs.jsonl"))
    rows.append({**rows[1], "id": "c2"})
    (run_dir / "runs.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def test_compare_page_lists_candidates(tmp_path):
    root, home = study_with_runs(tmp_path)
    run_dir = root / "runs" / RUN
    finished_run(run_dir, decision="use")
    _as_bakeoff(run_dir)
    verdict = build_verdict(root, RUN)
    assert [(c["ref"], c["decision"]) for c in verdict["candidates"]] == [("same", "keep"), ("restart", "use")]
    assert all(set(c) >= {"ref", "commit", "decision", "line", "mean_pct", "ci95_pct", "worst_pct", "chance"}
               for c in verdict["candidates"])
    line = verdict["candidates"][1]["line"]
    assert verdict["decision"] == "use" and verdict["line"] == f"Of 2 branches, restart: {line[0].lower()}{line[1:]}", \
        "the run's line is its chosen candidate's, said as one of two"
    assert verdict["candidates"][0]["mean_pct"] == -0.5 and verdict["candidates"][0]["line"].startswith("Not proven")
    assert "2 branches were compared; the more are compared, the likelier one looks ahead by chance." in render_report(verdict)
    plain_root, _ = study_with_runs(tmp_path / "plain")
    finished_run(plain_root / "runs" / RUN)
    assert "candidates" not in build_verdict(plain_root, RUN), "a search run has no candidates key"
    server = AppServer(home.root, port=0)
    server.start()
    try:
        status, body = call(server, "GET", f"/api/studies/{root.name}/verdict?run={RUN}")
        assert status == 200 and body["candidates"] == verdict["candidates"]
        status, body = call(server, "GET", f"/api/studies/{root.name}/results?run={RUN}")
        assert status == 200 and body["final"]["line"] == verdict["line"] and body["candidates"] == verdict["candidates"]
        status, body = call(server, "POST", f"/api/studies/{root.name}/bakeoff", {"refs": []})
        assert status == 400 and "refs: name at least one branch" in body["error"]
        status, body = call(server, "POST", f"/api/studies/{root.name}/bakeoff", {"refs": ["main"]})
        assert status == 409 and "this study tunes settings" in body["error"]
    finally:
        server.stop()
    js = (STATIC / "js" / "pages" / "compare.js").read_text(encoding="utf-8")
    view = js[js.index("function candidatesView"):js.index("function", js.index("function candidatesView") + 10)]
    assert "c.line" in view and "c.ref" in view and "EK.answerWords" not in view and "EK.better" not in view
    assert "query.run" in js and "/verdict?run=" in js
    study_js = (STATIC / "js" / "pages" / "study.js").read_text(encoding="utf-8")
    assert '"Compare branches"' in study_js and '"/bakeoff"' in study_js and "#/compare?run=" in study_js


NUMBERS = ("mean_pct", "ci95_pct", "worst_pct", "chance", "chance_words")


def test_a_bakeoff_with_no_branch_shown_ahead_keeps_todays(tmp_path):
    """Review ruling: with no branch `use`, no unnamed branch speaks for the run -- no top-level
    figures, no held-back "best" rows, no "what changed"; each branch's figures are in `candidates`."""
    from test_app_results import document_of

    root, _ = study_with_runs(tmp_path)
    run_dir = root / "runs" / RUN
    finished_run(run_dir, decision="keep", mean=0.4)
    _as_bakeoff(run_dir, {"c1": "keep", "c2": "keep"})
    verdict = build_verdict(root, RUN)
    assert verdict["decision"] == "keep"
    assert verdict["line"] == "None of the 2 branches is shown to beat the starting point."
    assert [c["decision"] for c in verdict["candidates"]] == ["keep", "keep"]
    assert all(c["line"].startswith("Not proven") and c["mean_pct"] is not None for c in verdict["candidates"])
    assert not set(NUMBERS) & set(verdict) and "levels" not in verdict
    assert verdict["by_case"] == [] and verdict["changed"] == []
    page = jobs.results(root, RUN, document_of)
    assert page["final"]["line"] == verdict["line"] and page["final"]["advice"] == verdict["advice"]
    assert not {"mean", "ci95", "worst_pct", "chance", "chance_words"} & set(page["final"])
    assert page["by_case"]["held"] == [] and page["changed"] == []
    assert all(k["test"]["best"] is None for k in page["kpis"])
    report = render_report(verdict)
    assert "Chance of a gain" not in report and "Change in" not in report.split("## Branches compared")[0]
    assert "No branch speaks for the run" in report


@pytest.mark.parametrize("decision", ["use", "keep", "single", "pending", "stopped"])
def test_page_line_equals_verdict_line_on_a_bakeoff(tmp_path, decision):
    """The page-equals-document rule (design 3.4) holds for a bake-off too."""
    from test_app_results import document_of

    root, _ = study_with_runs(tmp_path)
    run_dir = root / "runs" / RUN
    finished_run(run_dir, decision="use" if decision in ("use", "pending", "stopped") else decision,
                 check=decision != "pending" and decision != "stopped")
    if decision == "stopped":
        job = json.loads((run_dir / "job.json").read_text(encoding="utf-8"))
        job["final"] = {"stopped": True}
        (run_dir / "job.json").write_text(json.dumps(job), encoding="utf-8")
    _as_bakeoff(run_dir, {"use": None, "keep": {"c1": "keep", "c2": "keep"},
                          "single": {"c1": "single", "c2": "single"}}.get(decision))
    verdict = build_verdict(root, RUN)
    page = jobs.results(root, RUN, document_of)
    assert verdict["decision"] == decision
    assert page["final"]["line"] == verdict["line"] and page["final"]["advice"] == verdict["advice"]
    if decision in ("pending", "stopped"):
        # Review item 7: no answer yet, so no branch carries figures.
        assert [c["decision"] for c in verdict["candidates"]] == [decision, decision]
        assert all(not {"mean_pct", "ci95_pct", "worst_pct", "chance"} & set(c) for c in verdict["candidates"])
        assert not set(NUMBERS) & set(verdict)
    if decision == "use":
        assert verdict["advice"] == "Use the code of restart: it is probably better on cases no branch was made for."
    if decision == "single":
        line = next(c["line"] for c in verdict["candidates"] if c["ref"] == "restart")
        assert verdict["line"] == f"Of 2 branches, restart: {line[0].lower()}{line[1:]}" and line.startswith("Not proven, one")


def test_one_branch_is_named_without_of_one(tmp_path):
    root, _ = study_with_runs(tmp_path)
    run_dir = root / "runs" / RUN
    finished_run(run_dir, decision="use")
    _as_bakeoff(run_dir, {"c1": "use"}, only="c1")
    verdict = build_verdict(root, RUN)
    line = verdict["candidates"][0]["line"]
    assert verdict["line"] == f"restart: {line[0].lower()}{line[1:]}"
    _as_bakeoff(run_dir, {"c1": "keep"}, only="c1")
    assert build_verdict(root, RUN)["line"] == "restart is not shown to beat the starting point."


def test_a_failed_bakeoff_starts_again_as_itself(tmp_path):
    """Review item 2: a bake-off that failed before it recorded its branches still knows them
    (`bakeoff.json`), so "Start it again" posts them to /bakeoff, never /start; its strip says the
    branches were not compared, and a stopped one says so in its sentence."""
    from evolvekit.status import build_status

    root, _ = study_with_runs(tmp_path)
    run_dir = root / "runs" / RUN
    run_dir.mkdir(parents=True)
    (run_dir / "bakeoff.json").write_text(json.dumps({"refs": ["same", "restart"]}), encoding="utf-8")
    (run_dir / "job.json").write_text(json.dumps({"phase": "failed", "error": "HarnessError: x", "pid": 2 ** 22 + 7,
                                                  "started_at": "2026-01-01T00:00:00+00:00"}), encoding="utf-8")
    status = jobs.status(root, RUN, build_status)
    assert status["bakeoff_refs"] == ["same", "restart"] and status["can_resume"] is False
    job = json.loads((run_dir / "job.json").read_text(encoding="utf-8"))
    job["phase"] = "stopped"
    job["search"] = {"stop_reason": "stopped on request before the check began", "generations": 0, "aborted": False}
    (run_dir / "job.json").write_text(json.dumps(job), encoding="utf-8")
    assert jobs.status(root, RUN, build_status)["sentence"] == "Stopped before the branches were compared."
    plain_root, _ = study_with_runs(tmp_path / "plain")
    finished_run(plain_root / "runs" / RUN)
    assert jobs.status(plain_root, RUN, build_status)["bakeoff_refs"] is None
    study_js = (STATIC / "js" / "pages" / "study.js").read_text(encoding="utf-8")
    restart = study_js[study_js.index("restart: function"):study_js.index('"continue": function')]
    assert '"/bakeoff"' in restart and "bakeoff_refs" in restart and '"/start"' in restart
    assert "The branches were not compared" in study_js and "Comparing branches" in study_js


def test_outside_files_are_named_relative_to_the_source_folder(tmp_path):
    """Review item 6: the refusal names files as `open`'s globs do, relative to the source folder."""
    outer = tmp_path / "outer repo"
    shutil.copytree(ROOT / "examples" / "cli-solver", outer / "src", ignore=shutil.ignore_patterns("fresh", "__pycache__"))
    git_init_commit(outer)
    git(outer, "switch", "-q", "-c", "readme")
    (outer / "src" / "README.md").write_text("x\n")
    git(outer, "commit", "-q", "-am", "r")
    git(outer, "switch", "-q", "main")
    folder = _code_study(tmp_path, outer / "src")
    study, harness = load_study(folder), load_harness(folder / "harness")
    with pytest.raises(AppError, match=r"readme changes files outside the open files: README\.md; widen"):
        jobs.check_refs(folder, study, harness, ["readme"])
