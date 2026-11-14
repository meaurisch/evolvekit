"""The automatic plan (`evolvekit/harness/plan.py`), against hand-computed cases."""

from __future__ import annotations

import math
from datetime import datetime

import pytest

from evolvekit.harness.plan import Plan, make_plan


def _plan(**kwargs):
    defaults = dict(training=5, test=3, runs_per_case=1, time_limit_s=20, overhead_s=1, hours=2, workers=3,
                    pin_cpus=[2, 4, 6])
    return make_plan(**{**defaults, **kwargs})


def test_without_screening_every_combination_runs_on_every_case():
    plan = _plan()
    # T = 21; one check seed costs ceil(2*3/3) * 21 = 42 and 15 % of 2 h (1080 s)
    # pays for 25: capped at k = 12. C = ceil(2*3*12/3) * 21 = 504; S = 0.95 *
    # 7200 - 504 = 6336; B = ceil(5/3) * 21 = 42; R(12) = ceil(12*5/3) * 21 = 420
    # -> 14.99 rounds.
    assert (plan.run_s, plan.final_check_s, plan.search_s, plan.seed_round_s) == (21, 504, 6336, 42)
    assert (plan.children, plan.round_s, plan.rounds) == (12, 420, 14)
    assert plan.generations == 15 + 2 and not plan.screening
    assert plan.max_hours == pytest.approx(6336 / 3600)
    assert (plan.workers, plan.pin_cpus, plan.check_seeds) == (3, (2, 4, 6), 12)


def test_with_six_cases_or_more_a_screen_comes_first():
    plan = _plan(training=9)
    # q = min(4, ceil(9/3)) = 3 at a quarter of the limit (5 s + 1 s);
    # R(12) = ceil(12*3/3) * 6 + ceil(2*9/3) * 21 = 72 + 126 = 198.
    assert plan.screening and (plan.screen_cases, plan.screen_time_limit_s, plan.screen_run_s) == (3, 5.0, 6.0)
    assert (plan.children, plan.round_s) == (12, 198)
    assert plan.seed_round_s == 63 and plan.rounds == int((6336 - 63) / 198)


@pytest.mark.parametrize("kwargs", [dict(training=5), dict(training=9, time_limit_s=8), dict(training=9, accepts_time_limit=False)])
def test_no_screen_without_six_cases_and_a_ten_second_limit_the_application_keeps(kwargs):
    assert not _plan(**kwargs).screening


def test_fewer_combinations_when_twelve_would_leave_fewer_than_six_rounds():
    plan = _plan(hours=0.5)
    # 15 % of half an hour (270 s) pays for 6 check seeds at 42 s: C = ceil(36/3)
    # * 21 = 252, S = 1710 - 252 = 1458, B = 42: c = 12 gives (1458-42)/420 = 3.4
    # rounds, c = 6 (ceil(30/3)*21 = 210) gives 6.7 -- the largest with at least 6.
    assert (plan.check_seeds, plan.children, plan.rounds) == (6, 6, 6)


def test_three_rounds_are_accepted_when_six_are_out_of_reach():
    plan = _plan(hours=0.35)
    # S = 1197 - 126 = 1071; c = 4: ceil(20/3)*21 = 147 -> 7.0 rounds... c = 12: 2.4
    assert plan.rounds >= 3 and not plan.blocked


def test_two_check_seeds_when_three_do_not_fit():
    plan = _plan(training=3, test=6, time_limit_s=60, overhead_s=0, hours=0.5)
    # k = 3: C = ceil(36/3)*60 = 720, S = 990, B = 60, R(4) = 240 -> 3.9 rounds: fits (3+).
    assert plan.check_seeds == 3
    tight = _plan(training=3, test=6, time_limit_s=60, overhead_s=0, hours=0.42)
    # k = 3: C = 720, S = 1436.4 - 720 = 716.4 -> (716.4-60)/240 = 2.7 < 3;
    # k = 2: C = 480, S = 956.4 -> 3.7 rounds.
    assert tight.check_seeds == 2 and not tight.blocked and tight.rounds == 3


def test_a_plan_with_fewer_than_three_rounds_is_blocked_with_three_ways_out():
    plan = _plan(training=10, time_limit_s=120, hours=1)
    # T = 121, C = 726 (k = 2: 484), B = 484, R(4) = 6 * 31 + 7 * 121 = 1033: 2.1 (2.4) rounds.
    assert plan.blocked.startswith("With these choices the search gets fewer than 3 rounds")
    assert plan.ways_out[0] == "Let the search learn from at most 7 cases."
    assert plan.ways_out[1].startswith("Give each case at most ") and plan.ways_out[2].startswith("Allow at least ")
    assert len(plan.ways_out) == 3 and plan.summary() == plan.blocked


def test_a_blocked_plans_time_fixes_are_buttons_with_their_values():
    # Persona p5, J4: "Give each case at most 19 s" was text to retype under Time; now it is a button that sets
    # the value, and the reason sentence is said once (`blocked_reason`, the ways after it).
    plan = _plan(training=10, time_limit_s=20, hours=0.1)
    assert plan.blocked
    fixes = {fix["kind"]: fix for fix in plan.way_fixes}
    seconds = fixes["time_per_case"]["seconds"]
    assert f"Give each case at most {seconds:g} s." in plan.ways_out
    assert fixes["time_per_case"]["label"] == f"Give each case {seconds:g} s"
    minutes = round(fixes["hours"]["hours"] * 60)
    assert plan.ways_out[-1] == f"Allow at least {minutes} minutes in total."
    assert fixes["hours"]["label"] == f"Allow {minutes} minutes"
    assert not _plan(training=10, time_limit_s=20, hours=fixes["hours"]["hours"]).blocked, "the button's value works"
    data = plan.to_json()
    assert data["blocked_reason"] == "With these choices the search gets fewer than 3 rounds, too few to learn anything."
    assert data["way_fixes"] == [dict(f) for f in plan.way_fixes]
    assert not any(fix["label"].startswith("Let the search") for fix in data["way_fixes"]), "which cases is a choice"
    assert _plan().to_json()["blocked_reason"] == "" and _plan().way_fixes == ()


def test_the_last_way_out_is_said_in_minutes_below_an_hour_and_a_half():
    short = _plan(training=10, time_limit_s=20, hours=0.1)
    assert short.blocked and short.ways_out[-1].endswith("minutes in total.")
    assert _plan(training=10, time_limit_s=120, hours=1).ways_out[-1].endswith("hours in total.")


def test_an_expert_who_sets_the_rounds_is_not_blocked():
    blocked = _plan(training=10, time_limit_s=120, hours=1)
    assert blocked.blocked
    decided = _plan(training=10, time_limit_s=120, hours=1, overrides={"children": 2, "generations": 2})
    assert not decided.blocked and decided.ways_out == () and decided.generations == 2


def test_when_the_final_check_alone_is_too_long_the_way_out_is_fewer_test_cases():
    plan = _plan(training=2, test=10, time_limit_s=60, hours=0.5)
    # Two workers; the final check alone is ceil(60/2) x 61 s = 30.5 min, more
    # than the half hour: no number of training cases can help. With 5 test
    # cases it is 15 x 61 s, which leaves (795 - 61) / 244 = 3.0 rounds.
    assert plan.blocked and plan.ways_out[0] == "Hold back at most 5 cases for the final check."
    assert not any(way.startswith("Use at most ") for way in plan.ways_out)


def test_only_ways_out_that_work_are_offered():
    plan = _plan(training=10, time_limit_s=600, hours=1)
    # Ten 10-minute cases do not fit an hour whatever the sets: only a shorter
    # time per case or more hours help.
    assert [way.split(" at ")[0] for way in plan.ways_out] == ["Give each case", "Allow"]


def test_the_plan_in_words():
    plan = _plan()
    text = plan.summary(datetime(2026, 9, 26, 15, 40))
    assert text == (
        "about 14 rounds, about 168 combinations tried, a final check on 3 held-back cases with 12 runs each "
        "(≈ 8 min); done around 17:40."
    )
    assert "no final check" in _plan(test=0).summary(datetime(2026, 9, 26, 15, 40))


def test_an_expert_can_adjust_the_numbers():
    plan = _plan(overrides={"children": 5, "generations": 9, "workers": 2})
    assert (plan.children, plan.generations, plan.workers, plan.pin_cpus) == (5, 9, 2, (2, 4))
    assert set(plan.adjusted) == {"children", "generations", "workers"}


def test_workers_never_exceed_the_runs():
    plan = _plan(training=2, workers=7, pin_cpus=[1, 2, 3, 4, 5, 6, 7])
    assert plan.workers == 2 and plan.pin_cpus == (1, 2)


def test_an_application_that_does_not_stop_itself_is_planned_on_what_it_took():
    plan = _plan(accepts_time_limit=False, measured_s=12, time_limit_s=60)
    assert plan.run_s == 12
    assert _plan(accepts_time_limit=False, measured_s=90, time_limit_s=60).run_s == 60


def test_a_long_study_gives_its_check_more_runs_per_case():
    plan = make_plan(training=5, test=3, runs_per_case=1, time_limit_s=20, hours=2.0, workers=4)
    # One check seed costs ceil(2*3/4) * 20 = 40 s; 15 % of two hours is 1080 s:
    # 27 would fit, the twelve seeds there are cap it. C = ceil(72/4) * 20 = 360,
    # S = 6840 - 360 = 6480, B = 40, R(12) = 300 -> 21.5 rounds.
    assert plan.check_seeds == 12 and plan.rounds >= 6
    assert "with 12 runs each" in plan.summary()


def test_a_short_study_gives_its_check_fewer_runs_per_case_never_below_three():
    half_hour = make_plan(training=5, test=3, runs_per_case=1, time_limit_s=20, hours=0.5, workers=4)
    # 15 % of half an hour is 270 s: floor(270 / 40) = 6 seeds. C = ceil(36/4) * 20
    # = 180, S = 1710 - 180 = 1530, B = 40; c = 9 (ceil(45/4) * 20 = 240) gives
    # 6.2 rounds, the first with at least 6, so six seeds stand.
    assert half_hour.check_seeds == 6 and half_hour.rounds >= 6 and not half_hour.blocked
    quarter_hour = make_plan(training=5, test=3, runs_per_case=1, time_limit_s=20, hours=0.25, workers=4)
    # 135 s buys 3 seeds (3.4): the floor of three; C = 100, S = 755, c = 4 -> 7.1 rounds.
    assert quarter_hour.check_seeds == 3 and quarter_hour.rounds >= 6


GATE = dict(training=5, test=2, runs_per_case=1, time_limit_s=2, overhead_s=0.294, hours=0.167, workers=3,
            pin_cpus=[2, 4, 6])
"""Persona round 2's code study: 5 cases learned from, 2 held back, 2 s a case, ten minutes."""


def test_the_plan_of_a_study_with_ai_counts_the_time_of_each_ai_change():
    """Persona round 2, D1: the plan promised about 11 rounds and 132 combinations; 2 rounds and 25 ran,
    because each AI change took time the plan ignored (agent_patch about 19 s, diff about 8 s)."""
    from evolvekit.harness.compile import OPERATORS_CODE, OPERATORS_CODE_AGENT, OPERATORS_WITH_AI
    from evolvekit.harness.plan import AGENT_CHANGE_S, AI_CHANGE_S, ai_seconds

    assert (AI_CHANGE_S, AGENT_CHANGE_S) == (10.0, 30.0)
    assert ai_seconds(OPERATORS_CODE_AGENT) == (20.0, 20.0)  # half agent_patch, half diff: 20 s a child
    assert ai_seconds(OPERATORS_CODE) == (10.0, 10.0)
    assert ai_seconds(OPERATORS_WITH_AI) == pytest.approx((3.0, 10.0))  # 3 in 10 children ask the AI
    assert ai_seconds({"param_lhs": 1.0}) == (0.0, 0.0) and ai_seconds(None) == (0.0, 0.0)

    before = make_plan(**GATE)
    assert (before.children, before.rounds, before.ai_change_s) == (12, 11, 0.0), "without AI: today's plan"
    plan = make_plan(**GATE, operators=OPERATORS_CODE_AGENT)
    # T = 2.294; with 20 s of AI a child, R(c) = ceil(5c/3) * T + 20c. No c reaches 6 rounds at any number of
    # check seeds (k = 3: C = 4T, S = 571.14 - 9.18 = 561.96, B = 2T, R(4) = 7T + 80 = 96.06 -> 5.8), so 3 rounds
    # with k = 3: c = 7 gives R = 12T + 140 = 167.53 -> 3.3; c = 8 gives 14T + 160 = 192.12 -> 2.9.
    assert (plan.check_seeds, plan.children, plan.rounds, plan.combinations) == (3, 7, 3, 21)
    assert plan.round_s == pytest.approx(12 * 2.294 + 140) and plan.ai_change_s == 20.0
    assert plan.to_json()["ai_change_s"] == 20.0
    settings = make_plan(**GATE, operators=OPERATORS_WITH_AI)  # a settings study with AI: 3 s a child
    assert settings.round_s == pytest.approx(math.ceil(settings.children * 5 / 3) * 2.294 + 3.0 * settings.children)
    assert settings.ai_change_s == 10.0


def test_the_plan_line_names_the_ai_assumption():
    from evolvekit.harness.compile import OPERATORS_CODE_AGENT

    plan = make_plan(**GATE, operators=OPERATORS_CODE_AGENT)
    text = plan.summary(datetime(2026, 11, 8, 5, 38))
    assert text.startswith("about 3 rounds, about 21 combinations tried (assumes about 20 s for each AI change), "
                           "a final check on 2 held-back cases with 3 runs each")
    assert "assumes about" not in make_plan(**GATE).summary(), "no AI, no assumption"


THIN = "With 2 held-back cases a 'use' is unlikely: the worst case stays wide. Hold back more cases for a firmer answer."


def test_too_few_held_back_cases_for_a_firm_answer_is_a_note_with_the_plan():
    """Persona round 2, D2: nobody said before the run that 2 held-back cases can practically never reach
    "use"; examples/cli-solver/README.md says so ("Hold back more instances than that for a claim")."""
    from evolvekit.harness.plan import THIN_CHECK_CASES, plan_notes

    assert THIN_CHECK_CASES == 3 and plan_notes(2) == [THIN]
    assert plan_notes(1) == [THIN.replace("2 held-back cases", "1 held-back case")]
    assert plan_notes(0) == [] and plan_notes(3) == [], "no final check, or enough cases: no note"
    plan = make_plan(**GATE)
    assert plan.notes == [THIN] and plan.to_json()["notes"] == [THIN]
    assert plan.summary(datetime(2026, 11, 8, 5, 38)).endswith("; done around 05:48. " + THIN)
    assert THIN not in _plan().summary(), "three held back: no note"


# -- the plan re-estimated from the rounds measured (A4.1, P3) ----------------

def _record(generation, duration_s, breed_s=10.0, children=4, *, legacy=False, adopted=False):
    from evolvekit.harness.plan import RoundRecord

    return RoundRecord(generation, duration_s, breed_s, children, -9.0, None, 0.0, None, None, legacy, adopted)


def test_replan_reads_the_median_round_and_the_measured_ai_seconds():
    from dataclasses import replace

    from evolvekit.harness.plan import Measured, Plan, RoundRecord, replan

    plan = replace(Plan.fixed(children=4), search_s=3600.0)
    records = [RoundRecord(0, 50.0, 0.0, 0, -10.0, None, 0.0, None, None, False)] + [
        RoundRecord(g, d, 10.0, 4, -9.0, None, 0.0, None, None, False) for g, d in ((1, 100.0), (2, 120.0), (3, 300.0))]
    got = replan(plan, Measured(tuple(records), used_s=600.0))
    # eval median(90, 110, 290) = 110; AI 30 s over 12 children = 2.5 s; a round 110 + 4 * 2.5 = 120 s
    assert got.measured_round_s == 120.0 and got.measured_ai_child_s == 2.5
    assert got.rounds_done == 3 and got.rounds_left == 25 and got.rounds == plan.rounds
    assert got.to_json()["rounds_left"] == 25 and got.to_json()["measured"] == {"round_s": 120.0, "ai_child_s": 2.5}
    assert (got.round_s, got.ai_child_s, got.children, got.search_s) == (
        plan.round_s, plan.ai_child_s, plan.children, plan.search_s), "the planned numbers stay as planned"


def test_replan_before_the_first_round_changes_nothing():
    from evolvekit.harness.plan import Measured, Plan, replan

    plan = Plan.fixed()
    assert replan(plan, Measured((), used_s=0.0)) == plan and plan.to_json()["rounds_left"] is None
    assert plan.to_json()["rounds_done"] == 0 and plan.to_json()["measured"] is None
    assert replan(plan, Measured((_record(0, 50.0, 0.0, 0),), used_s=50.0)) == plan, "the starting point is no round"


def test_replan_leaves_adopted_and_legacy_rounds_out_of_the_ai_seconds():
    """Decision 9: an adopted round (its children were written by the session
    before: `adopted`) and a round from before A4 (no `breed_s`) paid no
    measured AI time; counting their children would understate it. A written
    round whose writing took under a millisecond still counts."""
    from dataclasses import replace

    from evolvekit.harness.plan import Measured, Plan, replan

    plan = replace(Plan.fixed(children=4), search_s=3600.0)
    records = (_record(1, 110.0, 10.0), _record(2, 90.0, 0.0, adopted=True), _record(3, 120.0, 0.0, legacy=True))
    got = replan(plan, Measured(records, used_s=1000.0))
    # AI 10 s over round 1's 4 children = 2.5 s; eval median(100, 90, 120) = 100; a round 100 + 4 * 2.5 = 110 s
    assert got.measured_ai_child_s == 2.5 and got.measured_round_s == 110.0
    assert got.rounds_done == 3 and got.rounds_left == 23  # floor(2600 / 110)
    only_adopted = replan(plan, Measured((_record(1, 90.0, 0.0, adopted=True),), used_s=100.0))
    assert only_adopted.measured_ai_child_s == 0.0 and only_adopted.measured_round_s == 90.0
    quick = replan(plan, Measured((_record(1, 100.0, 0.0), _record(2, 100.0, 8.0)), used_s=0.0))
    assert quick.measured_ai_child_s == 1.0, "a written round of 0.000 s counts its children: 8 s over 8"
    spent = replan(plan, Measured((_record(1, 90.0, 0.0),), used_s=4000.0))
    assert spent.rounds_left == 0, "never fewer than none"


def test_round_records_read_legacy_events_without_inventing_progress():
    from evolvekit.harness.plan import round_records

    events = [{"type": "generation_finished", "generation": 1, "duration_s": 5.0, "children": 4, "best_fitness": -3.0}]
    (r,) = round_records(events)
    assert r.legacy and r.best == -3.0 and r.breed_s == 0.0 and r.noise_sd == 0.0 and r.tpe_ratio is None
    assert r.rebased is None and r.breadth_at_ceiling is None and not r.adopted


def test_round_records_count_a_resumed_round_once_from_its_last_event():
    from evolvekit.harness.plan import round_records

    events = [
        {"type": "generation_finished", "generation": 2, "duration_s": 7.0, "children": 4, "best_fitness": -1.0,
         "round_best": -2.0, "breed_s": 1.5, "noise_sd": 0.3, "tpe_ratio": None, "breadth_at_ceiling": None},
        {"type": "generation_started", "generation": 1},
        {"type": "generation_finished", "generation": 1, "duration_s": 5.0, "children": 4, "best_fitness": -3.0,
         "round_best": -3.0, "breed_s": 1.0, "noise_sd": 0.2, "tpe_ratio": 0.5, "breadth_at_ceiling": False},
        {"type": "generation_finished", "generation": 1, "duration_s": 6.0, "children": 4, "best_fitness": -3.0,
         "round_best": -3.0, "breed_s": 0.0, "adopted": True, "noise_sd": 0.2, "tpe_ratio": None,
         "breadth_at_ceiling": False},
        {"type": "generation_finished", "generation": "x"},
    ]
    first, second = round_records(events)
    assert (first.generation, first.duration_s, first.breed_s, first.legacy, first.adopted) == (1, 6.0, 0.0, False, True)
    assert not second.adopted
    assert (second.best, second.rebased, second.noise_sd) == (-2.0, -1.0, 0.3), "a re-evaluation pooled the best"
    assert first.rebased is None and first.breadth_at_ceiling is False


def _events(path, rows):
    import json

    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _finished(generation, ts, **fields):
    return {"type": "generation_finished", "session": "s", "ts": f"2026-11-14T10:{ts}+00:00", "generation": generation,
            "duration_s": 100.0, "breed_s": 10.0, "adopted": False, "children": 4, "round_best": 1.0,
            "best_fitness": 1.0, **fields}


def test_the_round_hook_writes_the_replan_beside_the_planned_numbers(tmp_path):
    from dataclasses import replace

    from evolvekit.harness.execute import Job, _round_hook, read_job

    _events(tmp_path / "events.jsonl", [_finished(0, "00:00", breed_s=0.0, children=1), _finished(1, "01:40")])
    job = Job(tmp_path, run_id="r", study="s", plan={"rounds": 7, "children": 4, "search_s": 3600.0})
    plan = replace(Plan.fixed(children=4), search_s=3600.0)
    assert _round_hook(tmp_path, job, plan, uses_tpe=False, log=lambda _m: None)(1) is None
    # 100 s used; a round 90 + 4 * 2.5 = 100 s -> floor(3500 / 100) = 35
    written = read_job(tmp_path)["plan"]
    assert written == {"rounds": 7, "children": 4, "search_s": 3600.0, "rounds_done": 1, "rounds_left": 35,
                       "measured": {"round_s": 100.0, "ai_child_s": 2.5}}


def test_the_round_hook_logs_a_file_it_cannot_read_or_write_and_still_answers(tmp_path, monkeypatch):
    from dataclasses import replace

    from evolvekit import events as events_module
    from evolvekit.harness.execute import Job, _round_hook

    _events(tmp_path / "events.jsonl", [_finished(1, "01:40")])
    job = Job(tmp_path, run_id="r", study="s", plan={"rounds": 7})
    plan = replace(Plan.fixed(children=4), search_s=3600.0)
    said: list[str] = []

    def refuse(**_fields):
        raise OSError("disk full")

    monkeypatch.setattr(job, "update", refuse)
    assert _round_hook(tmp_path, job, plan, uses_tpe=False, log=said.append)(1) is None
    assert len(said) == 1 and "job.json could not be rewritten" in said[0] and "disk full" in said[0]

    def unreadable(_run_dir):
        raise OSError("locked")

    monkeypatch.setattr(events_module, "read_events", unreadable)
    assert _round_hook(tmp_path, job, plan, uses_tpe=False, log=said.append)(2) is None
    assert "events.jsonl could not be read" in said[1] and "locked" in said[1]


def test_a_resumed_run_replans_from_its_stored_plan_else_from_the_compiled_run():
    from types import SimpleNamespace

    from evolvekit.harness.execute import _stored_plan

    config = SimpleNamespace(budget=SimpleNamespace(max_hours=0.5), search=SimpleNamespace(children_per_generation=5))
    stored = _stored_plan({"children": 6, "search_s": 1200.0, "rounds": 3}, config)
    assert (stored.children, stored.search_s) == (6, 1200.0)
    fallback = _stored_plan({}, config)
    assert (fallback.children, fallback.search_s) == (5, 1800.0)
    odd = _stored_plan({"children": 0, "search_s": "x"}, SimpleNamespace(budget=SimpleNamespace(max_hours=None),
                                                                        search=SimpleNamespace(children_per_generation=4)))
    assert (odd.children, odd.search_s) == (4, 0.0)
