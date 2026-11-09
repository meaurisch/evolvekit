"""The verdict document (`evolvekit/app/verdict.py`): one document for the
final check's answer, that the page, `study verdict` and `study report`
render from -- on hand-written runs, and once on a real demo-tour study."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from test_app_results import document_of, finished_run, study_with_runs

from evolvekit.app import jobs
from evolvekit.app.verdict import DECISIONS, _chance_words, build_verdict

RUN = "20260101-000000"
START_STAYED_BEST = "the starting point stayed the best: there is nothing to check"
NOTHING_HELD_BACK = "no cases were held back, so there is no final check"


def _job(run_dir: Path, final: dict) -> None:
    """Rewrites the run's `job.json` with `final` in place of the final check."""
    job = json.loads((run_dir / "job.json").read_text(encoding="utf-8"))
    job["final"] = final
    (run_dir / "job.json").write_text(json.dumps(job), encoding="utf-8")


def _run_of(tmp_path: Path, decision: str) -> tuple[Path, str]:
    """A study whose one run ends in `decision`, written by hand."""
    root, _ = study_with_runs(tmp_path)
    run_dir = root / "runs" / RUN
    if decision in ("use", "keep", "worse", "single"):
        finished_run(run_dir, decision=decision)
    elif decision == "pending":
        finished_run(run_dir, check=False)
    elif decision == "stopped":
        finished_run(run_dir, check=False)
        _job(run_dir, {"stopped": True})
    elif decision == "skipped":
        finished_run(run_dir, check=False)
        _job(run_dir, {"skipped": NOTHING_HELD_BACK})
    elif decision == "none":
        finished_run(run_dir, check=False)
        _job(run_dir, {"skipped": START_STAYED_BEST})
    else:
        raise AssertionError(decision)
    return root, RUN


@pytest.fixture
def run_of(tmp_path):
    return lambda decision: _run_of(tmp_path, decision)


@pytest.fixture
def use_run(tmp_path):
    return _run_of(tmp_path, "use")


def test_verdict_keys_and_schema(use_run):
    root, run = use_run
    v = build_verdict(root, run)
    assert v["schema"] == 1 and v["decision"] in DECISIONS
    assert set(v) >= {"study", "run", "phase", "line", "advice", "measure", "held_back", "rule", "by_case", "changed", "files"}
    assert v["study"] == root.name and v["run"] == run and v["phase"] == "done" and v["decision"] == "use"
    assert v["measure"]["kpi"] == "tour_length" and v["measure"]["direction"] == "lower"
    assert v["held_back"] == {"cases": 1, "runs_per_case": 3}
    assert v["learned_from"]["cases"] == 3 and v["learned_from"]["pct"] == pytest.approx(4.2)
    assert v["mean_pct"] == pytest.approx(4.2) and v["chance"] == pytest.approx(0.97)
    assert [row["outcome"] for row in v["by_case"]] == ["better"]
    assert {c["name"] for c in v["changed"]} == {"restart_after", "accept_worse"}
    assert all(set(c) == {"name", "label", "from", "to"} for c in v["changed"])
    assert v["started_at"] and v["finished_at"] and v["elapsed_s"] == pytest.approx(12 * 60)
    assert v["rounds"] == 2 and v["combinations"] == 2
    assert build_verdict(root) == v, "the latest run, when none is named"


@pytest.mark.parametrize("decision", ["use", "keep", "worse", "single", "pending", "stopped", "skipped", "none"])
def test_page_line_equals_verdict_line(decision, run_of):
    root, run = run_of(decision)
    v = build_verdict(root, run)
    assert v["decision"] == decision
    r = jobs.results(root, run, document_of)
    assert r["final"]["line"] == v["line"] and r["final"]["advice"] == v["advice"]


@pytest.mark.parametrize("decision", ["keep", "worse", "single", "pending", "stopped", "skipped", "none"])
def test_advice_says_keep_todays_settings_unless_use(decision, run_of):
    """The action outright, as the app says it: anything but `use` keeps today's settings, with why after the colon."""
    v = build_verdict(*run_of(decision))
    assert v["advice"].startswith("Keep today's settings"), v["advice"]
    assert ": " in v["advice"], "the reason after the colon"


def test_page_held_back_rows_equal_the_verdict_rows(use_run):
    """One figure: the page's held-back table takes its change from the verdict
    document (the paired mean over the final check's runs), not from the two means."""
    root, run = use_run
    path = root / "runs" / run / "confirm" / "final" / "comparison.json"
    comparison = json.loads(path.read_text(encoding="utf-8"))
    held = comparison["instances"][0]
    comparison["per_candidate"]["c1"]["per_instance"] = [
        {"instance": held, "pairs": 3, "baseline": 50.0, "candidate": 47.9, "improvement_pct": 4.78}]
    path.write_text(json.dumps(comparison), encoding="utf-8")
    v = build_verdict(root, run)
    rows = jobs.results(root, run, document_of)["by_case"]["held"]
    assert [r["case"] for r in rows] == [r["name"] for r in v["by_case"]]
    for page, doc in zip(rows, v["by_case"]):
        assert {k: page[k] for k in ("start", "best", "change_pct")} == {k: doc[k] for k in ("start", "best", "change_pct")}
    assert rows[0]["change_pct"] == pytest.approx(4.78)


def test_results_learned_figure_is_the_verdict_learned_from(use_run):
    """The chosen combination's chip reads the verdict's own learned-from figure
    (the change of the plain averages), not the search's average of per-case changes."""
    root, run = use_run
    path = root / "runs" / run / "runs.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows[0]["kpis"].update({"tour_length": 1.0, "tour_length_raw": 100.0})
    rows[1]["kpis"].update({"tour_length": 0.9257, "tour_length_raw": 92.5})
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    v = build_verdict(root, run)
    r = jobs.results(root, run, document_of)
    assert v["learned_from"]["pct"] == pytest.approx(7.5)
    assert r["learned_pct"] == v["learned_from"]["pct"]
    assert r["improvement_pct"] == pytest.approx(4.2), "the search's own figure stays for the Tries plot"


def test_advice_for_use_says_use(use_run):
    assert build_verdict(*use_run)["advice"].startswith("Use the new settings")


def test_verdict_combinations_count_the_starting_point(use_run):
    """`combinations` counts every combination measured, the starting point among them (stable meaning)."""
    v = build_verdict(*use_run)
    assert v["combinations"] == 2, "the starting point and c1"


def test_verdict_pending_has_no_numbers(run_of):
    v = build_verdict(*run_of("pending"))
    assert v["decision"] == "pending"
    assert not {"mean_pct", "ci95_pct", "worst_pct", "chance", "chance_words"} & set(v)


def test_verdict_stopped_check(run_of):
    v = build_verdict(*run_of("stopped"))
    assert v["decision"] == "stopped" and "mean_pct" not in v


def test_verdict_skipped_names_reason(run_of):
    v = build_verdict(*run_of("skipped"))
    assert v["decision"] == "skipped" and v["reason"].startswith("no cases") and "mean_pct" not in v


def test_verdict_none_when_start_stayed_best(run_of):
    v = build_verdict(*run_of("none"))
    assert v["decision"] == "none" and v["reason"] == START_STAYED_BEST


def test_verdict_single_case(run_of):
    """The decision first, as every other decision leads with its word; the advice says why."""
    v = build_verdict(*run_of("single"))
    assert v["decision"] == "single" and v["line"].startswith("Not proven, one held-back case: ")
    assert v["line"].endswith(" lower tour length."), v["line"]
    assert "one held-back case is too few cases to tell" in v["advice"]


@pytest.mark.parametrize("decision", ["use", "keep", "worse", "single", "skipped", "none"])
def test_home_card_carries_the_verdict_line_and_advice(decision, run_of):
    """One world: the Home card's figure line and second line are the verdict's
    own, and its label is the state line's words, never "better" unless the
    verdict's line says so."""
    root, run = run_of(decision)
    v = build_verdict(root, run)
    card = jobs.run_line(root, None, root / "runs" / run, document_of)
    assert card["verdict"]["line"] == v["line"] and card["verdict"]["advice"] == v["advice"]
    assert card["verdict"]["decision"] == v["decision"]
    says = card["verdict"]["says"]
    assert card["line"].startswith("finished · " + says), (card["line"], says)
    if "better" not in v["line"].lower():
        assert "better" not in says
    assert jobs.run_line(root, None, root / "runs" / run).get("verdict") is None, "a list of runs does not pay for it"


def test_verdict_levels_mark_the_deciding_level(use_run):
    root, run = use_run
    path = root / "runs" / run / "confirm" / "final" / "comparison.json"
    comparison = json.loads(path.read_text(encoding="utf-8"))
    row = {"direction": "minimize", "unit": "percent", "n": 1, "wins": 1, "losses": 0, "clear": True}
    comparison["per_candidate"]["c1"]["levels"] = {
        "levels": [{**row, "level": 1, "kpi": "tour_length", "mean_difference": 0.0, "ci95": None, "state": "equal"},
                   {**row, "level": 2, "kpi": "longest_leg", "mean_difference": 3.3, "ci95": [1.0, 5.6], "state": "better"}],
        "deciding": 2, "verdict": "equal on level 1 (tour_length within 1 %), better on level 2 (longest_leg): clear",
        "confirmed": True}
    path.write_text(json.dumps(comparison), encoding="utf-8")
    v = build_verdict(root, run)
    assert sum(level["deciding"] for level in v["levels"]) == 1
    assert [level["kpi"] for level in v["levels"] if level["deciding"]] == ["longest_leg"]
    assert v["measure"]["kpi"] == "longest_leg" and v["decision"] == "use"
    assert jobs.results(root, run, document_of)["final"]["line"] == v["line"]


def test_verdict_never_contains_nan(use_run):
    root, run = use_run
    path = root / "runs" / run / "confirm" / "final" / "comparison.json"
    comparison = json.loads(path.read_text(encoding="utf-8"))
    comparison["per_candidate"]["c1"]["summary"]["worst"] = float("nan")
    path.write_text(json.dumps(comparison), encoding="utf-8")
    v = build_verdict(root, run)
    json.dumps(v, allow_nan=False)


def test_verdict_files_list_existing_downloads(use_run):
    root, run = use_run
    assert build_verdict(root, run)["files"] == []
    folder = root / "runs" / run / "downloads"
    folder.mkdir()
    (folder / "settings.json").write_text("{}", encoding="utf-8")
    (folder / "values.json").write_text("{}", encoding="utf-8")
    assert build_verdict(root, run)["files"] == [f"runs/{run}/downloads/settings.json"], "relative to the study folder"


@pytest.mark.parametrize("p,words", [(0.996, "almost certain"), (0.95, "about 19 in 20"), (0.897, "under 9 in 10"),
                                     (0.5, "about 10 in 20"), (0.04, "1 in 20 or less"), (None, "")])
def test_chance_words_table(p, words):
    assert _chance_words(p) == words


@pytest.mark.slow
def test_final_check_writes_exports(tmp_path):
    from evolvekit.harness.execute import run_study
    from evolvekit.harness.library import create_study
    from evolvekit.harness.study import load_study, save_study

    repo = Path(__file__).resolve().parents[1]
    demo = repo / "harnesses" / "demo-tour"
    folder = tmp_path / "study"
    create_study(folder, demo, "Demo settings", "tune")
    for sample in sorted((demo / "samples").glob("*.json")):
        shutil.copy(sample, folder / "cases" / sample.name)
    study = load_study(folder)
    study.application_path = str(repo / "examples" / "cli-solver" / "solver.py")
    study.training = ["cases/north-south-30.json", "cases/town-40.json", "cases/county-50.json"]
    study.test = ["cases/region-60.json"]
    study.limits.time_per_case_s = 0.5
    study.budget.hours = 0.01
    study.plan = {"auto": False, "children": 4, "generations": 3}
    save_study(study, folder)
    job = run_study(folder, "r1", log=lambda _m: None)
    assert job["phase"] == "done", job
    downloads = folder / "runs" / "r1" / "downloads"
    assert (downloads / "settings_json.json").is_file() and (downloads / "settings_flags.txt").is_file()
    assert "--neighbours" in (downloads / "settings_flags.txt").read_text(encoding="utf-8")
    v = build_verdict(folder, "r1")
    assert {"runs/r1/downloads/settings_json.json", "runs/r1/downloads/settings_flags.txt"} <= set(v["files"])
