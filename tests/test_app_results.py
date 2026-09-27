"""The results page in words (`evolvekit/app/jobs.py`): the final check and
what to do about it, a check with more runs, the goal case by case, the
failures, and a summary to paste -- on made-up runs, without running any."""

from __future__ import annotations

from pathlib import Path

import pytest

from evolvekit.app import jobs

GOAL = {"kpi": "cost", "says": "real cost", "label": "Real cost", "direction": "lower"}
PLAN = {"workers": 3, "run_s": 20.4}


def comparison(ci, mean=2.0, cases=3, seeds=3):
    return {"seeds": list(range(1001, 1001 + seeds)), "instances": ["a", "b", "c"][:cases],
            "per_candidate": {"c1": {"summary": {"n": cases, "mean": mean, "ci95": ci}}}}


def words(ci, mean=2.0, what="settings", baseline="", **job):
    return jobs._final_words({"final": {"candidate": "c1", **job}}, comparison(ci, mean), GOAL, what, baseline)


def test_the_final_check_says_what_to_do():
    unclear = words([-10.1, 11.5], 0.7)
    assert unclear["state"] == "unclear"
    assert unclear["line"] == ("Not distinguishable from your starting point on 3 held-back cases "
                               "(somewhere between 10.1 % worse and 11.5 % better).")
    assert unclear["advice"] == "Keep your current settings for now: the gain is not proven."
    confirmed = words([1.2, 5.0], 3.1)
    assert confirmed["line"] == "Confirmed: 3.1 % lower real cost on 3 held-back cases (likely between 1.2 % and 5.0 % better)."
    assert confirmed["advice"] == "Use the new settings: they are better on cases the search never saw."
    worse = words([-8.0, -1.0], -4.0)
    assert worse["line"] == "Worse on 3 held-back cases: 4.0 % higher real cost (likely between 1.0 % and 8.0 % worse)."
    assert worse["advice"] == "Keep your current settings: the new settings did worse on cases the search never saw."
    assert words(None, 2.0)["advice"].startswith("Not sure: one held-back case is too few")
    assert words([-1.0, 2.0], what="data changes")["advice"].startswith("Keep your data as it is")
    assert words([-1.0, 2.0], baseline="PyVRP's own defaults")["advice"] == \
        "Keep your current settings (PyVRP's own defaults) for now: the gain is not proven.", "named, when it is known"
    kept = jobs._final_words({"final": {"skipped": "the starting point stayed the best: there is nothing to check"}},
                             None, GOAL, "settings")
    assert kept["advice"] == "Keep your current settings: the search found nothing better."


def test_an_open_answer_can_be_checked_again_with_twice_the_runs():
    offer = jobs._recheck({"state": "unclear"}, PLAN, comparison([-1.0, 2.0]))
    assert offer["seeds"] == 6
    assert offer["seconds"] == pytest.approx(6 * 20.4), "2 sides x 3 cases x 3 more seeds, 3 at a time"
    assert offer["label"] == "Check again with 6 runs per case instead of 3 (about 2 min)"
    budget = {**PLAN, "hours": 0.5}
    within = jobs._recheck({"state": "unclear"}, budget, comparison([-1.0, 2.0]), used_s=25 * 60)
    assert within["why"].startswith("The study then takes about 27 min of its 30 min.") and not within["over"]
    over = jobs._recheck({"state": "unclear"}, budget, comparison([-1.0, 2.0]), used_s=29 * 60)
    assert over["why"].startswith("The study then takes about 31 min: more than its 30 min.") and over["over"]
    assert jobs._recheck({"state": "confirmed"}, PLAN, comparison([1.0, 2.0])) is None, "a settled answer"
    assert jobs._recheck({"state": "unclear"}, PLAN, comparison([-1.0, 2.0], seeds=12)) is None, "no seeds left"


def test_the_goal_case_by_case_counts_only_runs_both_finished():
    runs = [
        {"configuration": "baseline", "instance": "a", "seed": 1, "ok": True, "kpis": {"cost": 100.0}},
        {"configuration": "c1", "instance": "a", "seed": 1, "ok": True, "kpis": {"cost": 90.0}},
        {"configuration": "baseline", "instance": "a", "seed": 2, "ok": True, "kpis": {"cost": 110.0}},
        {"configuration": "c1", "instance": "a", "seed": 2, "ok": False, "kpis": {}},
        {"configuration": "baseline", "instance": "b", "seed": 1, "ok": True, "kpis": {"cost": 50.0}},
        {"configuration": "c1", "instance": "b", "seed": 1, "ok": True, "kpis": {"cost": 55.0}},
    ]
    assert jobs._by_case(runs, "baseline", "c1", "cost", "lower") == [
        {"case": "a", "start": 100.0, "best": 90.0, "runs": 1, "failed": 1, "change_pct": 10.0},
        {"case": "b", "start": 50.0, "best": 55.0, "runs": 1, "failed": 0, "change_pct": -10.0},
    ]
    start, best = jobs._means(jobs._paired(runs, "baseline", "c1"))
    assert (start, best) == ({"cost": 75.0}, {"cost": 72.5}), "the unfinished pair counts on neither side"


def test_a_label_inside_a_sentence():
    assert jobs._in_sentence("Real cost") == "real cost"
    assert jobs._in_sentence("Cost as PyVRP counts it") == "cost as PyVRP counts it"
    assert jobs._in_sentence("PyVRP's objective") == "PyVRP's objective"
    assert jobs._in_sentence("KPI total") == "KPI total"


def test_runs_that_overran_are_explained_against_the_time_limit():
    (said,) = jobs._failures_in_words([{"failure": "Timeout after 60s", "count": 13, "stage": "full"}], 20.0)
    assert said["count"] == 13
    assert said["message"] == "Did not come back in time: each case gets 20 s, and a run still going at 60 s is stopped"
    assert said["todo"].startswith("Your time limit is applied")
    (refused,) = jobs._failures_in_words([{"failure": "exit code 2", "count": 2, "stage": "full"}], 20.0)
    assert refused["message"] == "Broke a rule of the study" and refused["todo"].endswith("Nothing to do.")


def test_a_summary_to_paste():
    data = {
        "study": "PyVRP: tune solver settings", "harness": "PyVRP", "run": "20260927-005503",
        "headline": "The best settings found give 2.0 % lower real cost than your starting point on the cases the search learned from.",
        "final": words([-10.1, 11.5], 0.7),
        "context": {"time_per_case_s": 20.0, "hours": 0.5},
        "cases": {"training": ["cases/a.json", "cases/b.json"], "test": ["cases/c.json"]},
        "changed": [
            {"name": "max_penalty", "label": "Highest penalty", "old_text": "100000", "new_text": "1087.78", "changed": True},
            {"name": "num_neighbours", "label": "Neighbours", "old_text": "50", "new_text": "50", "changed": False},
        ],
    }
    data["context"]["compared_with"] = "PyVRP's own defaults"
    assert jobs._summary_text(data).splitlines() == [
        "PyVRP: tune solver settings (PyVRP), run 20260927-005503",
        "Answer: Keep your current settings for now: the gain is not proven.",
        "Set-up: 20 s per case, 30 min in total; the search learned from 2 cases and was checked on 1 held-back case."
        " Compared with PyVRP's own defaults.",
        "Search: " + data["headline"],
        "Final check: " + data["final"]["line"],
        "What changed:",
        "- Highest penalty (max_penalty): 100000 -> 1087.78",
        "Tried, and kept as they were: num_neighbours.",
    ]


def test_values_are_written_as_people_write_them():
    assert [jobs._describe_value(v) for v in (1248920.0, 100000, 2455, 0.923184, 1087.78, True, False, 0.00001234)] == \
        ["1,248,920", "100,000", "2,455", "0.923184", "1,087.78", "on", "off", "1.23e-05"]


def test_the_starting_point_is_named():
    from evolvekit.harness.manifest import load_harness
    from evolvekit.harness.study import SettingChoice, Study

    harness = load_harness(Path(__file__).resolve().parents[1] / "harnesses" / "pyvrp")
    study = Study(name="x", harness_id="pyvrp", harness_version=harness.version)
    study.settings = {"max_penalty": SettingChoice("tune")}
    assert jobs._baseline(study, harness, {"params": {"max_penalty": 100000.0}}) == "PyVRP's own defaults"
    assert jobs._baseline(study, harness, {"params": {"max_penalty": 5000.0}}) == "your starting settings"
    study.inputs = {"solver_settings": "inputs/today.json"}
    assert jobs._baseline(study, harness, {"params": {}}) == "the settings in today.json"


def test_a_data_change_is_shown_in_the_values_it_makes_and_a_rule_compares_them(tmp_path):
    import sqlite3

    from evolvekit.app import assistant
    from evolvekit.app.store import Home
    from evolvekit.harness.execute import preview
    from evolvekit.harness.manifest import load_harness
    from evolvekit.harness.study import load_study

    home = Home(tmp_path / "home")
    slug = home.create_study("demo-tour", "tune", "Weights")
    solver = Path(__file__).resolve().parents[1] / "examples" / "cli-solver" / "solver.py"
    change = {"lever": "stop_weights", "column": "weight", "mode": "scale", "low": 0.5, "high": 2.0, "start": 1.0}
    home.save_step(slug, {"application": {"path": str(solver), "version": ""},
                          "limits": {"time_per_case_s": 0.5, "retries": 1, "runs_per_case": 1},
                          "vary": {"settings": {}, "data": {"north": {**change, "where": "tag = 'north'"},
                                                            "south": {**change, "where": "tag = 'south'"}}}})
    home.use_samples(slug)
    root = home.study_root(slug)
    assert preview(root)["ok"]
    study, harness = load_study(root), load_harness(root / "harness")
    db = sqlite3.connect(root / "preview" / "tables.sqlite")
    north = sorted({r[0] for r in db.execute("SELECT weight FROM orig_stops WHERE tag = 'north'")})
    south = db.execute("SELECT MIN(weight), MAX(weight) FROM orig_stops WHERE tag = 'south'").fetchone()
    db.close()
    assert jobs._today(root, harness, study.data["north"]) == north, "the untouched values, on the smallest case"
    assert jobs._changed_values([2.0, 3.0], "scale", 1.5) == [3.0, 4.5] and jobs._changed_values([2.0], "add", 1) == [3.0]
    assert jobs._span_text([2.0, 3.0]) == "2–3" and jobs._span_text([2.5]) == "2.5"
    # The rule "keep one value above another" is SQL over the changed data, as the page writes it.
    rule = lambda a, b: {"kind": "constraint", "says": f"{a} above {b}", "sql":  # noqa: E731
                         f"SELECT (SELECT MIN(\"weight\") FROM \"stops\" WHERE tag = '{a}') > (SELECT MAX(\"weight\") FROM \"stops\" WHERE tag = '{b}')"}
    holds = min(north) > south[1]
    checked = assistant.check_card(rule("north", "south"), study, harness, root, {})
    assert checked["ok"] is holds, checked
