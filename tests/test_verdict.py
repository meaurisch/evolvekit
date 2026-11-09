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
    assert v["schema"] == 2 and v["decision"] in DECISIONS
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
    assert r["improvement_pct"] == pytest.approx(7.5), "one search figure: the Tries plot reads the same one"


def test_the_search_figure_is_one_on_every_surface(use_run):
    """persona p5, K1: report.html said 3.8 % where the Study page said 1.8 % -- the status document's mean of
    per-case changes against the change of the plain averages. The page's status, the results, the HTML report
    and the Markdown (report and summary) give the verdict's learned-from figure."""
    from evolvekit.app.report import render_report

    root, run = use_run
    path = root / "runs" / run / "runs.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows[0]["kpis"].update({"tour_length": 1.0, "tour_length_raw": 100.0})
    rows[1]["kpis"].update({"tour_length": 0.9257, "tour_length_raw": 92.5})
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    v = build_verdict(root, run)
    page = jobs.results(root, run, document_of)
    assert v["learned_from"]["pct"] == pytest.approx(7.5)
    figure = "7.5 % lower"
    assert figure in jobs.status(root, run, document_of)["best"]["says"]
    for name, text in (("html report", jobs.report_html(page)), ("markdown report", render_report(v, page)),
                       ("summary", page["summary_text"])):
        assert "4.2 % lower tour length than" not in text, name
        assert figure in text, name


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


@pytest.mark.parametrize("p,words", [(0.996, "almost certain"), (0.95, "about 19 in 20"), (0.897, "about 18 in 20"), (0.947, "under 19 in 20"),
                                     (0.5, "about 10 in 20"), (0.04, "less than 1 in 20"), (None, ""),
                                     (0.98, "better than 19 in 20"), (0.96, "about 19 in 20"), (0.03, "less than 1 in 20")])
def test_chance_words_table(p, words):
    assert _chance_words(p) == words


SAME_SETTINGS = ("No difference in any run: every run of this change gave exactly today's result, so the study "
                 "may not exercise it: the setting may not reach the program.")


def _same_run(tmp_path: Path, *, flagged: bool, apart: bool = False) -> tuple[Path, str]:
    """A finished run whose final check gave the best exactly the starting point's result in every
    run, on two held-back cases. `flagged`: the comparison says so itself (`identical`), as `confirm`
    writes it now; else only its runs show it, as in a comparison written before. `apart`: one run
    differs, so it is an ordinary result."""
    root, run = _run_of(tmp_path, "keep")
    folder = root / "runs" / run / "confirm" / "final"
    seeds, cases = [1001, 1002, 1003], ["town-40", "region-60"]
    rows = []
    for case in cases:
        for seed in seeds:
            value = 50.0 + seed / 1000
            rows.append({"configuration": "baseline", "instance": case, "seed": seed, "ok": True, "kpis": {"tour_length": value}})
            rows.append({"configuration": "c1", "instance": case, "seed": seed, "ok": True,
                         "kpis": {"tour_length": value - (0.1 if apart and seed == 1003 else 0.0)}})
    (folder / "results.json").write_text(json.dumps(rows), encoding="utf-8")
    summary = {"n": 2, "mean": 0.0, "sd": 0.0, "ci95": [-0.0, 0.0], "chance": 0.5, "worst": -0.0}
    result = {"summary": summary, "per_instance": [{"instance": c, "improvement_pct": 0.0} for c in cases]}
    if flagged:
        result["identical"] = not apart
    (folder / "comparison.json").write_text(json.dumps({
        "objective": "tour_length", "direction": "minimize", "seeds": seeds, "instances": cases,
        "per_candidate": {"c1": result}}), encoding="utf-8")
    return root, run


@pytest.mark.parametrize("flagged", [True, False])
def test_a_change_no_run_told_from_today_has_no_figures(tmp_path, capsys, flagged):
    """Persona round 1, B2: every paired run gave exactly today's result, and the verdict still said
    "a chance of about 10 in 20 of a gain ... between -0.0 % worse and 0.0 % better". Now: keep, the
    line that says nothing changed in any run, and no chance or range anywhere -- the document, the
    page, the report and `study status`."""
    from evolvekit.app.report import render_report
    from evolvekit.cli import main

    root, run = _same_run(tmp_path, flagged=flagged)
    v = build_verdict(root, run)
    assert v["decision"] == "keep" and v["line"] == SAME_SETTINGS
    assert v["advice"] == "Keep today's settings (Demo tour's own defaults) for now: nothing in these runs tells the change from today's."
    assert not {"mean_pct", "ci95_pct", "worst_pct", "chance", "chance_words", "levels"} & set(v)
    page = jobs.results(root, run, document_of)["final"]
    assert page["line"] == SAME_SETTINGS and page.get("mean") is None and page.get("chance") is None
    report = render_report(v)
    assert SAME_SETTINGS in report and "95 % interval" not in report and "Chance of a gain" not in report
    capsys.readouterr()
    assert main(["study", "status", root.name, "--home", str(root.parents[1])]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "finished · no difference in any run" and out[1] == SAME_SETTINGS


def test_one_run_apart_is_an_ordinary_result(tmp_path):
    root, run = _same_run(tmp_path, flagged=False, apart=True)
    v = build_verdict(root, run)
    assert v["line"] != SAME_SETTINGS and v["line"].startswith("Not proven on 2 held-back cases: ")
    assert "chance" in v and "-0.0" not in v["line"], "F6: a bound that rounds to zero has no sign"


def test_the_no_difference_line_names_what_may_not_reach_the_program():
    from evolvekit.app.verdict import no_difference_line

    assert no_difference_line("code change") == (
        "No difference in any run: every run of this change gave exactly today's result, so the study may not "
        "exercise it: a fixed setting, or a code path these cases never reach, can leave a change without effect."), "persona round 2, D7: a code path these cases never reach, not only a fixed setting"
    assert no_difference_line("settings") == SAME_SETTINGS
    assert no_difference_line("data changes").endswith(" may not exercise it: the data change may not reach the program.")


@pytest.mark.parametrize("low,high,words", [
    (-0.0, 0.0, "somewhere between 0.0 % worse and 0.0 % better"),
    (-0.04, 0.03, "somewhere between 0.0 % worse and 0.0 % better"),
    (0.0, -0.0, "somewhere between 0.0 % worse and 0.0 % better"),
    (-1.26, 2.0, "somewhere between 1.3 % worse and 2.0 % better"),
    (-3.0, -0.04, "likely between 0.0 % and 3.0 % worse"),
])
def test_a_range_that_rounds_to_zero_never_says_minus_zero(low, high, words):
    """Persona round 1, F6: "-0.0 % worse"."""
    from evolvekit.app.report import _between
    from evolvekit.app.verdict import _span, number_text

    assert _span(low, high) == words
    assert "-0.0" not in _between(low, high, "lower") and "-0.0" not in _between(low, high, "higher")
    assert number_text(-0.04) == "0.0" and number_text(-0.06) == "-0.1" and number_text(-0.0, 3) == "0.000"


def _short_run(tmp_path: Path, *, ai: bool, stop: str, generations: int = 2) -> tuple[Path, str]:
    import yaml

    root, run = _run_of(tmp_path, "keep")
    run_dir = root / "runs" / run
    job = json.loads((run_dir / "job.json").read_text(encoding="utf-8"))
    job["plan"] = {"hours": 0.167, "rounds": 11, "combinations": 132}
    job["search"] = {"generations": generations, "stop_reason": stop, "aborted": False}
    (run_dir / "job.json").write_text(json.dumps(job), encoding="utf-8")
    with (run_dir / "runs.jsonl").open("a", encoding="utf-8") as handle:
        for n in range(23):  # 25 combinations, the starting point among them
            handle.write(json.dumps({"id": f"x{n}", "operator": "mutate", "score": -99.0, "kpis": {"tour_length": 99.0}}) + "\n")
    copy = run_dir / "study.yaml"
    data = yaml.safe_load((root / "study.yaml").read_text(encoding="utf-8"))
    data["budget"]["ai"] = {"enabled": ai, "max_usd": 1.0}
    copy.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return root, run


TIME_LIMIT = "time limit: another round would not finish within 0.1484 h (0.12 h used; a round has taken up to 0.07 h)"


def test_a_search_that_ran_short_of_its_plan_says_so(tmp_path, capsys):
    """Persona round 1, F2: the plan said 11 rounds and 132 combinations; 2 rounds and 25 ran, the time
    ran out, and nobody said so afterwards."""
    from evolvekit.app.report import render_report
    from evolvekit.cli import _say_verdict

    root, run = _short_run(tmp_path, ai=True, stop=TIME_LIMIT)
    v = build_verdict(root, run)
    said = ("Ran 2 of the 11 planned rounds (25 combinations, about 132 planned): the time ran out; "
            "the AI's replies took longer than the plan assumed.")
    assert v["how_it_ran"] == said and v["planned"] == {"rounds": 11, "combinations": 132}
    page = jobs.results(root, run, document_of)  # the Study page's Details say how it ran, in the same words
    assert page["how_it_ran"] == said and page["planned"] == {"rounds": 11, "combinations": 132}
    report = render_report(v)
    how = report.split("## How it ran", 1)[1]
    assert said.rstrip(".") in how and "- Rounds:" not in how and "- Combinations tried:" not in how
    capsys.readouterr()
    _say_verdict(v)
    assert capsys.readouterr().out.splitlines()[-2:] == [said, "AI: $0.00 of $1.00"], "then what the AI spent (D3)"
    assert build_verdict(*_short_run(tmp_path / "b", ai=False, stop=TIME_LIMIT))["how_it_ran"].endswith(
        "the time ran out; the rounds took longer than the plan assumed.")


def test_a_plan_that_happened_says_nothing_of_it(tmp_path):
    v = build_verdict(*_short_run(tmp_path, ai=True, stop=TIME_LIMIT, generations=11))
    assert "how_it_ran" not in v


@pytest.mark.parametrize("stop,generations,why", [
    ("stopped on request", 2, "stopped"),
    ("time limit reached: 0.17 h of active run time, budget.max_hours is 0.1484 h", 5, "the search time was used"),
    ("no improvement for 3 rounds", 4, "the search stopped early (no improvement for 3 rounds)"),
])
def test_fewer_rounds_than_planned_always_say_how_much_ran(tmp_path, stop, generations, why):
    """Persona p4, H7: how much of the plan ran, whenever fewer rounds ran than planned -- not only
    when the time ran out -- with the reason from the stop."""
    v = build_verdict(*_short_run(tmp_path, ai=False, stop=stop, generations=generations))
    assert v["how_it_ran"] == f"Ran {generations} of the 11 planned rounds (25 combinations, about 132 planned): {why}."


def test_combinations_that_repeated_one_already_tried_are_said(tmp_path):
    """Persona p4: every planned round ran, yet fewer combinations than planned -- the ones that
    repeated a combination already tried were not measured again."""
    root, run = _short_run(tmp_path, ai=False, stop="the plan's rounds are done", generations=11)
    with (root / "runs" / run / "runs.jsonl").open("a", encoding="utf-8") as handle:
        for n in range(3):
            handle.write(json.dumps({"id": f"d{n}", "operator": "param_cross", "rejected": True,
                                     "reject_reason": "duplicate: structurally identical to x1"}) + "\n")
    assert build_verdict(root, run)["how_it_ran"] == (
        "Ran all 11 planned rounds (25 combinations, about 132 planned); 3 of the combinations tried repeated "
        "one already tried and were not measured again."), "what it knows, not the whole shortfall (m6)"


def _two_held_back(tmp_path: Path, changes: tuple[float, ...] = (3.1, 8.2), ci: tuple[float, float] = (-26.7, 37.9),
                   decision: str = "keep", direction: str | None = None) -> tuple[Path, str]:
    """Persona p4's Monday answer: a keep on held-back cases that each came out better, with a range
    that still allows a loss."""
    import yaml

    root, run = _run_of(tmp_path, decision)
    run_dir = root / "runs" / run
    if direction is not None:
        data = yaml.safe_load((root / "study.yaml").read_text(encoding="utf-8"))
        data["goal"] = {"levels": [{"kpi": "tour_length", "direction": direction}]}
        (root / "study.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    cases = [f"cases/held-{i}.json" for i in range(len(changes))]
    folder = run_dir / "confirm" / "final"
    mean = sum(changes) / len(changes)
    folder.joinpath("comparison.json").write_text(json.dumps({
        "seeds": [1001, 1002, 1003], "instances": cases,
        "per_candidate": {"c1": {
            "summary": {"n": len(changes), "mean": mean, "ci95": list(ci), "chance": 0.85, "worst": ci[0]},
            "per_instance": [{"instance": c, "pairs": 3, "improvement_pct": x} for c, x in zip(cases, changes)]}},
    }), encoding="utf-8")
    rows = []
    for case, change in zip(cases, changes):
        for seed in (1001, 1002, 1003):
            rows.append({"configuration": "baseline", "instance": case, "seed": seed, "ok": True,
                         "kpis": {"tour_length": 50.0, "longest_leg": 3.0}})
            rows.append({"configuration": "c1", "instance": case, "seed": seed, "ok": True,
                         "kpis": {"tour_length": 50.0 * (1 - change / 100), "longest_leg": 3.0}})
    folder.joinpath("results.json").write_text(json.dumps(rows), encoding="utf-8")
    return root, run


WHY_KEEP = ("Every held-back case was lower (3.1 % and 8.2 %), but with only 2 cases the range is too wide "
            "to rule out a loss: hold back more cases for a firmer answer.")


def test_a_keep_whose_held_back_cases_all_improved_says_why(tmp_path, capsys):
    """Persona p4, H2: both held-back cases came out lower, yet the headline says "at worst 26.7 %
    worse". One plain sentence after the verdict's line says why it is still keep -- on the page,
    the card, the report and `study verdict`."""
    from evolvekit.app.report import render_report
    from evolvekit.cli import _say_verdict
    from evolvekit.harness.study import load_study

    root, run = _two_held_back(tmp_path)
    v = build_verdict(root, run)
    assert v["decision"] == "keep" and v["why_keep"] == WHY_KEEP
    page = jobs.results(root, run, document_of)
    assert page["why_keep"] == WHY_KEEP and page["final"]["why_keep"] == WHY_KEEP
    card = jobs.run_line(root, load_study(root), root / "runs" / run, document_of)
    assert card["verdict"]["why_keep"] == WHY_KEEP
    report = render_report(v).split("## Verdict", 1)[1]
    assert report.index(v["line"]) < report.index(WHY_KEEP) < report.index(v["advice"])
    capsys.readouterr()
    _say_verdict(v)
    assert capsys.readouterr().out.splitlines()[:3] == [v["line"], WHY_KEEP, v["advice"]]
    text = page["summary_text"].splitlines()  # the text to paste: right after the final check's line (I2)
    at = text.index(f"Final check: {v['line']}")
    assert text[at + 1] == WHY_KEEP


def test_why_keep_names_three_cases_at_most_and_the_goals_direction(tmp_path):
    three = build_verdict(*_two_held_back(tmp_path / "a", changes=(3.1, 4.0, 8.2), direction="higher"))
    assert three["why_keep"].startswith("Every held-back case was higher (3.1 %, 4.0 % and 8.2 %), but with only 3 cases")
    four = build_verdict(*_two_held_back(tmp_path / "b", changes=(3.1, 4.0, 8.2, 1.0)))
    assert four["why_keep"] == ("Every one of the 4 held-back cases was lower, but with only 4 cases the range is too "
                                "wide to rule out a loss: hold back more cases for a firmer answer.")


@pytest.mark.parametrize("changes,ci,decision", [
    ((3.1, -2.0), (-26.7, 37.9), "keep"),   # a case went the other way
    ((3.1, 8.2), (0.5, 9.0), "keep"),       # the range rules out a loss
    ((3.1, 8.2), (1.0, 9.0), "use"),
    ((-3.1, -8.2), (-12.0, -1.0), "worse"),
])
def test_why_keep_is_only_on_a_keep_that_could_still_be_a_loss(tmp_path, changes, ci, decision):
    v = build_verdict(*_two_held_back(tmp_path, changes=changes, ci=ci, decision=decision))
    assert "why_keep" not in v


RAW_SEED, RAW_BEST = 7.888410522186774, 7.408278547681725
"""The persona's run: the starting point's and the best's plain averages over the cases learned from."""


def test_the_best_so_far_is_one_figure_that_never_moves_while_the_best_stays(tmp_path, monkeypatch, capsys):
    """Persona round 1, F3: the app said "Best so far: 8.99 % better" while `study status` said "0.4 %
    lower", and the app's figure fell. One function gives the best so far: on the cases learned from,
    on the base of the verdict's learned-from figure -- the status document's `best.pct`, `study
    status`'s line and, once the run is done, `learned_from.pct` agree."""
    import os

    from evolvekit.app.verdict import best_so_far
    from evolvekit.cli import main
    from evolvekit.harness.study import load_study

    root, run = _run_of(tmp_path, "keep")
    run_dir = root / "runs" / run
    rows = [  # `tour_length` is each case as a percentage of the starting point's, averaged: another base
        {"id": "seed", "operator": "human-seed", "score": -100.0, "kpis": {"tour_length": 100.0, "tour_length_raw": RAW_SEED}},
        {"id": "g1", "operator": "mutate", "score": -99.6, "kpis": {"tour_length": 99.6, "tour_length_raw": 7.867}},
        {"id": "c1", "operator": "mutate", "score": -93.6, "kpis": {"tour_length": 93.6, "tour_length_raw": RAW_BEST}},
    ]
    (run_dir / "runs.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    learned = (RAW_SEED - RAW_BEST) / RAW_SEED * 100
    done = build_verdict(root, run)
    assert done["learned_from"]["pct"] == pytest.approx(learned)
    goal = jobs._goal(load_study(root), jobs.load_harness(root / "harness"))  # noqa: SLF001
    assert best_so_far(run_dir, goal) == {"id": "c1", "pct": pytest.approx(learned)}
    # The same run while it searches, with a status document whose own figure is on the other base.
    job = json.loads((run_dir / "job.json").read_text(encoding="utf-8"))
    job.update(phase="search", pid=os.getpid(), started_at=jobs._now().isoformat())  # noqa: SLF001
    job.pop("final"), job.pop("search")
    (run_dir / "job.json").write_text(json.dumps(job), encoding="utf-8")
    monkeypatch.setattr(jobs, "run_alive", lambda *a, **k: True)

    def status(_run_dir: Path) -> dict:
        return {"progress": {"best": {"id": "c1"}, "improvement": {"pct": 6.4, "verdict": "within noise"}}}

    best = jobs.status(root, run, status)["best"]
    assert best["pct"] == pytest.approx(learned)
    # The app's strip and card say it in `study status`'s words, from the server: never formatted twice.
    assert best["id"] == "c1" and best["says"] == "6.1 % lower tour length"
    card = jobs.run_line(root, load_study(root), run_dir, status)
    assert card["best_pct"] == pytest.approx(learned) and " · 6.1 % lower tour length so far, not checked yet" in card["line"]
    assert card["best_says"] == "6.1 % lower tour length"
    # A combination still being measured has no row yet: the figure stays until it has one.
    with (run_dir / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"type": "eval_finished", "candidate_id": "g2-half", "instance": "a", "ok": True,
                                 "kpis": {"tour_length": 1.0}}) + "\n")
    assert best_so_far(run_dir, goal)["pct"] == pytest.approx(learned)
    monkeypatch.setattr("evolvekit.cli.build_status", status)
    capsys.readouterr()
    assert main(["study", "status", root.name, "--home", str(root.parents[1])]) == 0
    assert "6.1 % lower tour length so far" in capsys.readouterr().out


@pytest.mark.parametrize("decision,checked", [("keep", True), ("pending", False), ("stopped", False)])
def test_a_finished_runs_best_is_said_in_finished_words(tmp_path, decision, checked):
    """Persona round 2, D5: after the verdict the Study page's Details still said "So far ... The results
    say whether it held". A finished run's best line is past: the search's best, on the cases it learned
    from, and whether the final check is the answer or it was never checked."""
    root, run = _run_of(tmp_path, decision)
    run_dir = root / "runs" / run
    rows = [
        {"id": "seed", "operator": "human-seed", "score": -100.0, "kpis": {"tour_length": 100.0, "tour_length_raw": RAW_SEED}},
        {"id": "c1", "operator": "mutate", "score": -93.6, "kpis": {"tour_length": 93.6, "tour_length_raw": RAW_BEST}},
    ]
    (run_dir / "runs.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    def status(_run_dir: Path) -> dict:
        return {"progress": {"best": {"id": "c1"}, "improvement": {"pct": 6.4, "verdict": "within noise"}}}

    line = jobs.status(root, run, status)["best"]["line"]
    assert not line.startswith("So far") and "The results say whether it held" not in line, line
    assert line.startswith("The search's best was 6.1 % lower tour length than the starting point"), line
    assert "on the 3 cases the search learned from" in line, line
    if checked:
        assert line.endswith("The final check's verdict is the answer on the cases the search never saw."), line
    else:
        assert line.endswith("It was not checked on cases the search never saw."), line


def test_a_done_run_without_its_comparison_does_not_point_to_a_final_check(tmp_path):
    """The verdict says "There is no final check for this run"; the best line must not name one."""
    root, run = _run_of(tmp_path, "keep")
    run_dir = root / "runs" / run
    rows = [
        {"id": "seed", "operator": "human-seed", "score": -100.0, "kpis": {"tour_length": 100.0, "tour_length_raw": RAW_SEED}},
        {"id": "c1", "operator": "mutate", "score": -93.6, "kpis": {"tour_length": 93.6, "tour_length_raw": RAW_BEST}},
    ]
    (run_dir / "runs.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    for found in run_dir.rglob("comparison.json"):
        found.unlink()

    def status(_run_dir: Path) -> dict:
        return {"progress": {"best": {"id": "c1"}, "improvement": {"pct": 6.4, "verdict": "within noise"}}}

    line = jobs.status(root, run, status)["best"]["line"]
    assert line.endswith("It was not checked on cases the search never saw."), line
    assert "final check" not in line, line


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


SPENT = [0.1308835, 0.084216, 0.0062953, 0.0539862, 0.0552962, 0.0546002, 0.0148953, 0.0072853, 0.0530502, 0.0568059,
         0.0107453, 0.0534011, 0.0297458, 0.051386, 0.00534, 0.0380658, 0.0511728, 0.00543, 0.0098885, 0.062312,
         0.0151285, 0.00568, 0.0130085, 0.00535]
"""Persona round 2's usage.jsonl: 24 AI changes, $0.87 in all of its $1 cap."""


def _usage(run_dir: Path, rows: list[dict]) -> None:
    (run_dir / "usage.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_the_ai_spend_is_a_fact_of_the_verdict_the_report_and_the_status(tmp_path, capsys):
    """Persona round 2, D3: "$0.87 of my $1, summed from usage.jsonl, is not shown anywhere". It is a fact,
    not a claim: the verdict document's `spent`, the report's How it ran, `study verdict`, `study status`."""
    from evolvekit.app.report import render_report
    from evolvekit.cli import main

    root, run = _short_run(tmp_path, ai=True, stop="no improvement for 3 rounds", generations=11)
    run_dir = root / "runs" / run
    _usage(run_dir, [{"operator": "diff", "provider": "claude-cli", "usd": usd} for usd in SPENT])
    v = build_verdict(root, run)
    assert v["spent"] == {"usd": pytest.approx(sum(SPENT)), "cap_usd": 1.0, "line": "AI: $0.87 of $1.00"}
    assert "- AI: $0.87 of $1.00" in render_report(v).split("## How it ran", 1)[1]
    assert jobs.results(root, run, document_of)["spent"] == v["spent"], "the page's Details read the same"
    home = str(root.parents[1])
    for argv in (["study", "verdict", root.name], ["study", "status", root.name]):
        capsys.readouterr()
        assert main([*argv, "--home", home]) == 0
        assert capsys.readouterr().out.splitlines()[-1] == "AI: $0.87 of $1.00"
    # A run that counts requests, not dollars: how many; a run without AI says nothing of it.
    _usage(run_dir, [{"operator": "diff", "provider": "copilot-cli", "usd": 0.0, "requests": 1.0}] * 3
           + [{"operator": "diff", "provider": "copilot-cli", "usd": 0.0}])
    assert build_verdict(root, run)["spent"] == {"requests": 4.0, "line": "AI: 4 requests"}
    assert "spent" not in build_verdict(*_short_run(tmp_path / "b", ai=False, stop="no improvement for 3 rounds"))


def test_a_live_search_carries_its_ai_spend(tmp_path, monkeypatch):
    """D3: the status document says the spend while the run searches, for the app's strip."""
    import os

    import yaml

    root, run = _short_run(tmp_path, ai=True, stop="")
    run_dir = root / "runs" / run
    job = json.loads((run_dir / "job.json").read_text(encoding="utf-8"))
    job.update({"phase": "search", "pid": os.getpid(), "started_at": jobs._now().isoformat()})  # noqa: SLF001
    job.pop("finished_at", None)
    (run_dir / "job.json").write_text(json.dumps(job), encoding="utf-8")
    monkeypatch.setattr(jobs, "run_alive", lambda *a, **k: True)
    _usage(run_dir, [{"operator": "agent_patch", "provider": "claude-cli", "usd": 0.42}])
    assert jobs.status(root, run, document_of)["spent"] == {"usd": 0.42, "cap_usd": 1.0, "line": "AI: $0.42 of $1.00"}
    copy = run_dir / "study.yaml"
    data = yaml.safe_load(copy.read_text(encoding="utf-8"))
    data["budget"]["ai"] = {"enabled": False}
    copy.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    assert jobs.status(root, run, document_of)["spent"] is None


def test_why_keep_is_never_given_when_the_goal_has_levels(tmp_path):
    """Fix round 1, I1: with goals in order of importance the range and the rows are the first
    measure's, while another level may be what kept it: no why_keep."""
    root, run = _two_held_back(tmp_path)
    path = root / "runs" / run / "confirm" / "final" / "comparison.json"
    comparison = json.loads(path.read_text(encoding="utf-8"))
    comparison["per_candidate"]["c1"]["levels"] = {
        "confirmed": False, "verdict": "equal on tour length, worse on longest leg", "deciding": 2,
        "levels": [{"level": 1, "kpi": "tour_length", "direction": "minimize", "state": "equal", "unit": "percent",
                    "mean_difference": 5.6, "ci95": [-26.7, 37.9]},
                   {"level": 2, "kpi": "longest_leg", "direction": "minimize", "state": "worse", "unit": "percent",
                    "mean_difference": -3.0, "ci95": [-5.0, -1.0]}]}
    path.write_text(json.dumps(comparison), encoding="utf-8")
    v = build_verdict(root, run)
    assert v["decision"] == "keep" and v.get("levels") and "why_keep" not in v
    assert "why_keep" not in jobs.results(root, run, document_of)["final"]


# -- a search that did not run (persona p5, J1) -----------------------------------

NOT_RUN = "the search did not run: there was not enough time left for its first round"


def _not_run(tmp_path: Path) -> tuple[Path, str]:
    """The intern's first run: the starting point measured, then the time limit refused round 1."""
    root, run = _run_of(tmp_path, "pending")
    run_dir = root / "runs" / run
    job = json.loads((run_dir / "job.json").read_text(encoding="utf-8"))
    job["plan"] = {"hours": 0.167, "rounds": 4, "combinations": 48}
    job["search"] = {"generations": 0, "stop_reason": "time limit: another round would not finish within 0.124 h "
                     "(0.01 h used; a round has taken up to 0.12 h)", "best_id": "seed", "aborted": False}
    job["final"] = {"skipped": NOT_RUN, "not_run": True}
    (run_dir / "job.json").write_text(json.dumps(job), encoding="utf-8")
    rows = [json.loads(line) for line in (run_dir / "runs.jsonl").read_text(encoding="utf-8").splitlines()]
    (run_dir / "runs.jsonl").write_text(json.dumps(rows[0]) + "\n", encoding="utf-8")  # the starting point only
    (run_dir / "evolvekit.yaml").write_text("{}\n", encoding="utf-8")  # compiled: the run can go on
    return root, run


def test_the_final_check_of_a_search_that_did_not_run_says_why(tmp_path):
    from evolvekit.harness.execute import Job, _final_check, not_run_words

    run_dir = tmp_path / "runs" / "r1"
    run_dir.mkdir(parents=True)
    (run_dir / "runs.jsonl").write_text('{"id": "seed", "operator": "human-seed", "score": -100.0}\n', encoding="utf-8")
    job = Job(run_dir, run_id="r1", study="s", plan={})
    job.update(search={"generations": 0, "aborted": False, "best_id": "seed",
                       "stop_reason": "time limit: another round would not finish within 0.124 h (0.01 h used; "
                                      "a round has taken up to 0.12 h)"})
    _final_check(None, None, None, run_dir, job, lambda line: None)  # type: ignore[arg-type]
    assert job.state["phase"] == "done" and job.state["final"] == {"skipped": NOT_RUN, "not_run": True}
    assert not_run_words("budget.max_full_evals_per_day reached: 94 of 94") == (
        "today's limit on full runs was reached before its first round")
    assert not_run_words("the AI budget was used up: $0.01 of $1.00 left") == "the AI budget was used up before its first round"


def test_a_search_that_did_not_run_never_says_it_found_nothing_better(tmp_path):
    from evolvekit.app.report import render_report
    from evolvekit.harness.study import load_study

    root, run = _not_run(tmp_path)
    v = build_verdict(root, run)
    assert v["line"] == "The search did not run: there was not enough time left for its first round."
    assert v["advice"] == ("Keep today's settings (Demo tour's own defaults) for now: nothing was searched yet. "
                           "Continue the run to give the search its time.")
    assert v["decision"] == "skipped" and v["not_run"] is True
    page = jobs.results(root, run, document_of)
    assert page["headline"] == v["line"] and page["final"]["not_run"] and page["can_continue"] is True
    words = [render_report(v, page), jobs.report_html(page), page["summary_text"]]
    card = jobs.run_line(root, load_study(root), root / "runs" / run, document_of)  # Past runs and Home's card
    assert card["line"] == "finished · the search did not run" and card["not_run"] and card["decision"] == "skipped"
    assert card["can_continue"] is True and card["continue_h"] > 0 and card["scheduled"] is False  # Home continues it
    assert card["verdict"]["line"] == v["line"]
    for text in words + [card["line"], card["verdict"]["advice"]]:
        assert "found nothing better" not in text and "stayed the best" not in text and "beat your starting" not in text
    assert all(v["line"] in text for text in words)
    assert v["how_it_ran"] == "None of the 4 planned rounds ran: only today's settings were measured."
    assert v["learned_from"]["pct"] is None, "a search that did not run learned nothing"
    ran = jobs.report_html(page)
    assert "Combinations tried" not in ran and "How it ran</div><div class='v'>None of the 4 planned rounds ran" in ran, "as the Markdown"
    counted = jobs.report_html({k: v for k, v in page.items() if k != "how_it_ran"})
    assert "Combinations tried</div><div class='v'>0, plus today&#x27;s settings" in counted, "M5"


@pytest.mark.parametrize("what,said", [
    ("settings", "only today's settings were measured"),
    ("code change", "only today's code was measured"),
    ("data changes", "only the data as it is was measured"),
    ("combination of settings and data changes", "only today's settings and data were measured"),
])
def test_a_search_that_did_not_run_names_what_was_measured(what, said):
    # Persona p5 fix round 1, M2: by what the study changes; nothing at all when the starting point was cut short.
    from evolvekit.app.verdict import how_it_ran

    job = {"search": {"generations": 0, "stop_reason": "time limit: its first round would not finish within 0.124 h"}}
    plan = {"rounds": 4, "combinations": 48}
    assert how_it_ran(job, plan, 1, False, what=what) == f"None of the 4 planned rounds ran: {said}."
    assert how_it_ran(job, plan, 0, False, what=what) == "None of the 4 planned rounds ran: nothing was measured."


def test_the_seed_cut_by_the_time_limit_is_said_in_its_own_words():
    from evolvekit.harness.execute import not_run_words

    assert not_run_words("time limit reached: 0.12 h of active run time, budget.max_hours is 0.124 h") == (
        "the time ran out before its first round")
    assert not_run_words("time limit: its first round would not finish within 0.124 h (…)") == (
        "there was not enough time left for its first round")


def test_a_time_below_the_harness_own_is_said_in_the_report_and_the_plan(use_run):
    """persona p5, K2: 5 s a case against the harness's 1 min was never said. Below the harness's own time per
    case the verdict, the HTML report, the Markdown and the summary say it, once, in the same words."""
    from evolvekit.app.report import render_report
    from evolvekit.harness.study import load_study, save_study

    root, run = use_run
    note = "Tuned at 1 s a case; Demo tour's own time is 2 s: settings found here may not hold at a longer time limit."
    assert "time_note" not in build_verdict(root, run), "the study's time is the harness's own"
    study = load_study(root)
    study.limits.time_per_case_s = 1
    save_study(study, root)
    v = build_verdict(root, run)
    page = jobs.results(root, run, document_of)
    assert v["time_note"] == note and page["time_note"] == note
    html = jobs.report_html(page)
    assert html.count("Tuned at 1 s a case") == 1 and "Demo tour&#x27;s own time is 2 s" in html
    assert note.rstrip(".") in render_report(v, page) and note in page["summary_text"]
    study.limits.time_per_case_s = 5
    save_study(study, root)
    assert "time_note" not in build_verdict(root, run), "above the harness's own time: nothing to say"


def test_a_check_limited_by_its_cases_says_so_in_place_of_more_runs(run_of):
    root, run = run_of("keep")
    job_path = root / "runs" / run / "job.json"
    job = json.loads(job_path.read_text(encoding="utf-8"))
    job["check"] = {"seeds": 3, "limited_by": "cases"}
    job_path.write_text(json.dumps(job), encoding="utf-8")
    v = build_verdict(root, run)
    assert v["limited_by"] == "cases"
    assert v["why_keep"] == ("The answer is set by the held-back cases, not by the runs: hold back more cases "
                             "for a firmer answer.")
    assert jobs.results(root, run, document_of)["final"]["line"] == v["line"]
