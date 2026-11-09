"""The automatic plan (`evolvekit/harness/plan.py`), against hand-computed cases."""

from __future__ import annotations

import math
from datetime import datetime

import pytest

from evolvekit.harness.plan import make_plan


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
