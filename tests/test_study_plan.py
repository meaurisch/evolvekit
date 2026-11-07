"""The automatic plan (`evolvekit/harness/plan.py`), against hand-computed cases."""

from __future__ import annotations

from datetime import datetime

import pytest

from evolvekit.harness.plan import make_plan


def _plan(**kwargs):
    defaults = dict(training=5, test=3, runs_per_case=1, time_limit_s=20, overhead_s=1, hours=2, workers=3,
                    pin_cpus=[2, 4, 6])
    return make_plan(**{**defaults, **kwargs})


def test_without_screening_every_combination_runs_on_every_case():
    plan = _plan()
    # T = 21; C = ceil(2*3*3/3) * 21 = 126; S = 0.95 * 7200 - 126 = 6714;
    # B = ceil(5/3) * 21 = 42; R(12) = ceil(12*5/3) * 21 = 420 -> 15.9 rounds.
    assert (plan.run_s, plan.final_check_s, plan.search_s, plan.seed_round_s) == (21, 126, 6714, 42)
    assert (plan.children, plan.round_s, plan.rounds) == (12, 420, 15)
    assert plan.generations == 16 + 2 and not plan.screening
    assert plan.max_hours == pytest.approx(6714 / 3600)
    assert (plan.workers, plan.pin_cpus, plan.check_seeds) == (3, (2, 4, 6), 3)


def test_with_six_cases_or_more_a_screen_comes_first():
    plan = _plan(training=9)
    # q = min(4, ceil(9/3)) = 3 at a quarter of the limit (5 s + 1 s);
    # R(12) = ceil(12*3/3) * 6 + ceil(2*9/3) * 21 = 72 + 126 = 198.
    assert plan.screening and (plan.screen_cases, plan.screen_time_limit_s, plan.screen_run_s) == (3, 5.0, 6.0)
    assert (plan.children, plan.round_s) == (12, 198)
    assert plan.seed_round_s == 63 and plan.rounds == int((6714 - 63) / 198)


@pytest.mark.parametrize("kwargs", [dict(training=5), dict(training=9, time_limit_s=8), dict(training=9, accepts_time_limit=False)])
def test_no_screen_without_six_cases_and_a_ten_second_limit_the_application_keeps(kwargs):
    assert not _plan(**kwargs).screening


def test_fewer_combinations_when_twelve_would_leave_fewer_than_six_rounds():
    plan = _plan(hours=0.5)
    # S = 1710 - 126 = 1584, B = 42: c = 12 gives (1584-42)/420 = 3.7 rounds,
    # c = 7 (ceil(35/3)*21 = 252) gives 6.1 -- the largest with at least 6.
    assert (plan.children, plan.rounds) == (7, 6)


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
    assert plan.ways_out[0] == "Use at most 7 training cases."
    assert plan.ways_out[1].startswith("Give each case at most ") and plan.ways_out[2].startswith("Allow at least ")
    assert len(plan.ways_out) == 3 and plan.summary() == plan.blocked


def test_when_the_final_check_alone_is_too_long_the_way_out_is_fewer_test_cases():
    plan = _plan(training=2, test=10, time_limit_s=60, hours=0.5)
    # Two workers; the final check alone is ceil(60/2) x 61 s = 30.5 min, more
    # than the half hour: no number of training cases can help. With 5 test
    # cases it is 15 x 61 s, which leaves (795 - 61) / 244 = 3.0 rounds.
    assert plan.blocked and plan.ways_out[0] == "Hold back at most 5 test cases for the final check."
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
        "about 15 rounds, about 180 combinations tried, a final check on 3 held-back cases (≈ 2 min); "
        "done around 17:40."
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
