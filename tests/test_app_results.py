"""The results page in words (`evolvekit/app/jobs.py`): the final check and
what to do about it, a check with more runs, the goal case by case, the
failures, and a summary to paste -- on made-up runs, without running any."""

from __future__ import annotations

import pytest

from evolvekit.app import jobs

GOAL = {"kpi": "cost", "says": "real cost", "label": "Real cost", "direction": "lower"}
PLAN = {"workers": 3, "run_s": 20.4}


def comparison(ci, mean=2.0, cases=3, seeds=3):
    return {"seeds": list(range(1001, 1001 + seeds)), "instances": ["a", "b", "c"][:cases],
            "per_candidate": {"c1": {"summary": {"n": cases, "mean": mean, "ci95": ci}}}}


def words(ci, mean=2.0, what="settings", **job):
    return jobs._final_words({"final": {"candidate": "c1", **job}}, comparison(ci, mean), GOAL, what)


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
    kept = jobs._final_words({"final": {"skipped": "the starting point stayed the best: there is nothing to check"}},
                             None, GOAL, "settings")
    assert kept["advice"] == "Keep your current settings: the search found nothing better."


def test_an_open_answer_can_be_checked_again_with_twice_the_runs():
    offer = jobs._recheck({"state": "unclear"}, PLAN, comparison([-1.0, 2.0]))
    assert offer["seeds"] == 6
    assert offer["seconds"] == pytest.approx(6 * 20.4), "2 sides x 3 cases x 3 more seeds, 3 at a time"
    assert offer["label"] == "Check again with 6 runs per case instead of 3 (about 2 min)"
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
    assert jobs._summary_text(data).splitlines() == [
        "PyVRP: tune solver settings (PyVRP), run 20260927-005503",
        "Set-up: 20 s per case, 30 min in total; the search learned from 2 cases and was checked on 1 held-back case.",
        "Result: " + data["headline"],
        "Final check: " + data["final"]["line"],
        "Advice: Keep your current settings for now: the gain is not proven.",
        "What changed:",
        "- Highest penalty (max_penalty): 100000 -> 1087.78",
        "Tried, and kept as they were: num_neighbours.",
    ]
