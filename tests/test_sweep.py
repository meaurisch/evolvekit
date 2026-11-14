"""D6, the sweep of small lies: each a promise the code did not keep."""

from __future__ import annotations

from dataclasses import replace

from evolvekit.budget import StopPolicy
from evolvekit.config import StopConfig, load_config
from evolvekit.prompts import OPERATOR_INSTRUCTIONS, big_step_reason
from evolvekit.providers.fake import FakeProvider
from evolvekit.search.driver import Driver
from tests.conftest import EXAMPLE_CONFIG


def _driver(tmp_path, responses=("no code here",), stop=None, **search):
    config = load_config(EXAMPLE_CONFIG)
    config = replace(config, search=replace(config.search, scratchpad_every=0, **search))
    if stop:
        config = replace(config, stop=replace(config.stop, **stop))
    provider = FakeProvider(list(responses), cycle=True)
    return Driver(config, run_dir=tmp_path / "run", providers={"small": provider, "strong": provider}), provider


def test_more_than_two_inspirations_are_drawn_when_asked_for(tmp_path):
    driver, _ = _driver(tmp_path, inspirations=4)
    wanted: list[int] = []
    driver.grid.sample_inspirations = lambda parent, n, rng: wanted.append(n) or []
    seed = driver._seed_candidate()
    for _ in range(200):
        driver._inspirations(seed, "diff")
    assert max(wanted) == 4 and min(wanted) == 1
    wanted.clear()
    for _ in range(50):
        driver._inspirations(seed, "crossover")
    assert set(wanted) == {1}, "crossover is shown one inspiration's code, never more"


# -- A0.6b: a big step says why, and counts once it applied -----------------

_WORST_FIT = (
    "```python\n"
    "def priority(item, bins):\n"
    '    """Worst fit."""\n'
    "    return [b - item for b in bins]\n"
    "```"
)


def _counts(policy: StopPolicy) -> tuple[int, int]:
    return policy.big_steps_in_window, policy.big_steps_applied_in_window


def test_the_big_step_says_which_trigger_fired():
    assert big_step_reason(scheduled=True, every=3, stagnant=0) == "This big step is scheduled: one every 3 generation(s)."
    assert big_step_reason(scheduled=False, every=0, stagnant=2) == (
        "This big step is due because the search has not improved for 2 generation(s)."
    )
    assert big_step_reason(scheduled=True, every=3, stagnant=4) == big_step_reason(
        scheduled=True, every=3, stagnant=0
    ), "scheduled wins when both triggers fire"
    assert "plateaued" not in OPERATOR_INSTRUCTIONS["big_step"]
    assert "scheduled" not in OPERATOR_INSTRUCTIONS["big_step"]


def test_a_plateau_big_step_is_told_it_is_a_plateau(tmp_path):
    driver, _ = _driver(tmp_path, big_step_every=99, children_per_generation=2)
    driver.stop_policy.stagnant = 2
    plan = driver._plan_operators(1, plateau=True)
    assert plan[0] == "big_step"
    assert driver._big_step_reason == big_step_reason(scheduled=False, every=99, stagnant=2)
    assert _counts(driver.stop_policy) == (1, 0), "planned is an attempt, not an applied step"


def test_a_big_step_on_both_triggers_says_scheduled(tmp_path):
    driver, _ = _driver(tmp_path, big_step_every=2, children_per_generation=2)
    driver.stop_policy.stagnant = 3
    assert driver._plan_operators(4, plateau=True)[0] == "big_step"
    assert driver._big_step_reason == "This big step is scheduled: one every 2 generation(s)."
    driver._plan_operators(3, plateau=False)
    assert driver._big_step_reason is None, "no big step planned, no reason"


def test_the_scheduled_big_step_prompt_names_its_schedule(tmp_path):
    driver, provider = _driver(tmp_path, big_step_every=1, generations=1, children_per_generation=1,
                               operators={"rewrite": 1.0}, inspirations=0)
    driver.run()
    prompts = [call["messages"][1]["content"] for call in provider.calls]
    assert any("This big step is scheduled: one every 1 generation(s)." in p for p in prompts)


def test_the_re_prompt_of_a_big_step_still_says_why_it_is_due(tmp_path):
    seed_block = _driver(tmp_path / "probe")[0].seed_block
    driver, provider = _driver(tmp_path, big_step_every=1, generations=1, children_per_generation=1,
                               operators={"rewrite": 1.0}, inspirations=0,
                               responses=[f"```python\n# Reworded, not changed.\n{seed_block}```"])
    driver.run()  # the big step hands back its parent reworded: a no-op, re-prompted once
    prompts = [call["messages"][1]["content"] for call in provider.calls]
    retries = [p for p in prompts if "structurally identical" in p and "This is a big step" in p]
    assert retries, "the big step's no-op was re-prompted"
    assert all("This big step is scheduled: one every 1 generation(s)." in p for p in retries)


def test_a_big_step_that_never_applied_is_not_counted_live_or_on_resume(tmp_path):
    driver, _ = _driver(tmp_path, big_step_every=1, generations=2, children_per_generation=1,
                        operators={"rewrite": 1.0}, inspirations=0)
    driver.run()  # every response is "no code here": nothing ever applies
    assert any(str(c.operator).startswith("big_step") for c in driver.archive)
    live = _counts(driver.stop_policy)
    assert live[1] == 0 and live[0] >= 1, "tried, never applied"
    again, _ = _driver(tmp_path, big_step_every=1, generations=2, children_per_generation=1,
                       operators={"rewrite": 1.0}, inspirations=0)
    again.resume()
    again._restore_stop_policy()
    assert _counts(again.stop_policy) == live


def test_a_big_step_that_applied_is_counted_the_same_live_and_on_resume(tmp_path):
    driver, _ = _driver(tmp_path, big_step_every=1, generations=2, children_per_generation=1,
                        operators={"rewrite": 1.0}, inspirations=0, responses=[_WORST_FIT])
    driver.run()
    live = _counts(driver.stop_policy)
    assert live[1] >= 1 and live[1] == live[0], "every big step applied (a twin still applied)"
    again, _ = _driver(tmp_path, big_step_every=1, generations=2, children_per_generation=1,
                       operators={"rewrite": 1.0}, inspirations=0, responses=[_WORST_FIT])
    again.resume()
    again._restore_stop_policy()
    assert _counts(again.stop_policy) == live


def test_a_failed_big_step_relieves_patience_but_not_the_economics_guard():
    policy = StopPolicy(StopConfig(patience=2, min_big_steps=1, max_usd_since_improvement=0.0))
    policy.update(1.0)
    policy.update(1.0)
    held = policy.update(1.0)
    assert not held.stop and "required big step" in held.reason
    policy.note_big_step()  # tried; its response never applied
    assert policy.big_steps_applied_in_window == 0

    class _Point:
        usd_since_improvement = 1.0
        gain_per_usd = None
        window_usd = 0.0

    assert policy.economics_reason(_Point()) is None, "the economics guard waits for an applied step"
    assert policy.update(1.0).stop is True, "patience counts the attempt"
    policy.note_big_step_applied()
    assert policy.economics_reason(_Point()) is not None


# -- A3.2: one plateau big step per `big_step_every` generations ------------


def _applied(driver, generation):  # what `_breed_one` does when a big step applies
    driver.stop_policy.note_big_step_applied()
    if driver._big_step_on_plateau:
        driver._plateau_step_at = generation


def test_a_plateau_takes_one_big_step_then_waits_big_step_every_generations(tmp_path):
    driver, _ = _driver(tmp_path, big_step_every=4, children_per_generation=2, stop={"patience": 4})
    taken = []
    for g, stagnant in zip(range(5, 14), range(2, 11)):
        driver.stop_policy.stagnant = stagnant
        if driver._plan_operators(g, plateau=True)[0] == "big_step":
            taken.append(g)
            _applied(driver, g)
    assert taken == [5, 8, 12], "a scheduled step on the plateau restarts the window"
    assert driver._plateau_step_at == 12


def test_an_improvement_since_the_last_big_step_lifts_the_spacing(tmp_path):
    driver, _ = _driver(tmp_path, big_step_every=99, children_per_generation=2, stop={"patience": 2})
    driver.stop_policy.stagnant = 1
    assert driver._plan_operators(5, plateau=True)[0] == "big_step"
    _applied(driver, 5)
    driver.stop_policy.stagnant = 2  # g5 and g6 flat: nothing improved since the step
    assert driver._plan_operators(7, plateau=True)[0] != "big_step"
    driver.stop_policy.stagnant = 1  # g5 improved, g6 flat
    assert driver._plan_operators(7, plateau=True)[0] == "big_step"


def test_a_big_step_that_did_not_apply_does_not_start_the_spacing(tmp_path):
    driver, _ = _driver(tmp_path, big_step_every=99, children_per_generation=2, stop={"patience": 4})
    driver.stop_policy.stagnant = 2
    assert driver._plan_operators(5, plateau=True)[0] == "big_step"  # its reply never applies
    driver.stop_policy.stagnant = 3
    assert driver._plan_operators(6, plateau=True)[0] == "big_step"
    _applied(driver, 6)
    driver.stop_policy.stagnant = 4
    assert driver._plan_operators(7, plateau=True)[0] != "big_step", "the applied one starts it"


def test_a_scheduled_step_off_the_plateau_does_not_start_the_spacing(tmp_path):
    driver, _ = _driver(tmp_path, big_step_every=4, children_per_generation=2, stop={"patience": 4})
    driver.stop_policy.stagnant = 1
    assert driver._plan_operators(4, plateau=False)[0] == "big_step"
    _applied(driver, 4)
    assert driver._plateau_step_at is None
    driver.stop_policy.stagnant = 2
    assert driver._plan_operators(5, plateau=True)[0] == "big_step"


def test_a_held_plateau_is_not_kept_waiting(tmp_path):
    driver, _ = _driver(tmp_path, big_step_every=99, children_per_generation=2,
                        stop={"patience": 4, "min_big_steps": 2})
    driver.stop_policy.stagnant = 6
    assert driver._plan_operators(5, plateau=True)[0] == "big_step"
    _applied(driver, 5)
    assert driver.stop_policy.big_steps_in_window == 1
    assert driver._plan_operators(6, plateau=True)[0] == "big_step", "the run cannot stop before the second"
    _applied(driver, 6)
    assert driver._plan_operators(7, plateau=True)[0] != "big_step", "two tried: the plateau is no longer held"


def _row(**fields):
    from evolvekit.candidate import Candidate

    base = dict(generation=0, source="", operator="seed", parent_id=None,
                stages_reached=["static", "proxy", "full"], competes=True)
    base.update(fields)
    return Candidate(**base)


def test_the_big_step_hint_names_the_previous_big_step_and_its_outcome(tmp_path):
    driver, _ = _driver(tmp_path, big_step_every=99, children_per_generation=2)
    seed = _row(id="g000-c0000", block=driver.seed_block, score=1.0)
    worst = _WORST_FIT.split("\n", 1)[1].rsplit("```", 1)[0]
    other = _row(id="g002-c0004", generation=2, block=worst + "# other\n", operator="diff",
                 parent_id=seed.id, score=0.5)
    step = _row(id="g004-c0013", generation=4, block=worst, operator="big_step/rewrite",
                parent_id=seed.id, score=0.96)
    later = _row(id="g005-c0014", generation=5, block=worst + "# later\n", operator="rewrite",
                 parent_id=seed.id, score=0.9)
    for row in (seed, other, step, later):
        driver.archive.append(row)
        driver.by_id[row.id] = row
    driver.stop_policy.stagnant = 5
    driver._plan_operators(9, plateau=True)
    assert driver._big_step_reason.startswith("This big step is due because the search has not improved for")
    assert "The previous big step: `g004-c0013` big_step/rewrite: +" in driver._big_step_reason
    assert driver._big_step_reason.endswith("-> -0.04.")


def test_the_first_big_step_hint_is_the_trigger_alone(tmp_path):
    driver, _ = _driver(tmp_path, big_step_every=3, children_per_generation=2)
    driver._plan_operators(3, plateau=False)
    assert driver._big_step_reason == "This big step is scheduled: one every 3 generation(s)."


def test_an_adopted_generation_counts_its_big_step(tmp_path):
    """`pending.json` children are adopted without `_plan_operators` or `_breed_one`:
    the two counts and the spacing still see their big step (A3 decision 5)."""
    driver, _ = _driver(tmp_path, big_step_every=99, children_per_generation=2)
    seed = _row(id="g000-c0000", block=driver.seed_block, score=1.0)
    driver.archive.append(seed)
    driver.by_id[seed.id] = seed
    worst = _WORST_FIT.split("\n", 1)[1].rsplit("```", 1)[0]
    applied = _row(id="g007-c0020", generation=7, block=worst, operator="big_step/rewrite",
                   parent_id=seed.id, stages_reached=[], competes=False)
    driver._write_pending(7, [applied])
    assert driver._adopt_pending(7, plateau=True)
    assert _counts(driver.stop_policy) == (1, 1) and driver._plateau_step_at == 7

    again, _ = _driver(tmp_path / "b", big_step_every=99, children_per_generation=2)
    again.archive.append(seed)
    again.by_id[seed.id] = seed
    failed = _row(id="g007-c0020", generation=7, block=seed.block, operator="big_step/big_step",
                  parent_id=seed.id, rejected=True, stages_reached=[], competes=False)
    again._write_pending(7, [failed])
    assert again._adopt_pending(7, plateau=True)
    assert _counts(again.stop_policy) == (1, 0) and again._plateau_step_at is None


def test_an_adopted_generation_counts_with_the_plateau_its_session_recorded(tmp_path):
    """The session that bred the pending children recorded the plateau it planned
    them with; the resumed session reads that, not its own (A3.2 review)."""
    worst = _WORST_FIT.split("\n", 1)[1].rsplit("```", 1)[0]
    for recorded, now, expected in ((False, True, None), (True, False, 7)):
        first, _ = _driver(tmp_path / str(recorded), big_step_every=99, children_per_generation=2)
        seed = _row(id="g000-c0000", block=first.seed_block, score=1.0)
        first._generation_started(7, children=1, plateau=recorded)
        first._write_pending(7, [_row(id="g007-c0020", generation=7, block=worst, operator="big_step/rewrite",
                                      parent_id=seed.id, stages_reached=[], competes=False)])
        again, _ = _driver(tmp_path / str(recorded), big_step_every=99, children_per_generation=2)
        again.archive.append(seed)
        again.by_id[seed.id] = seed
        again._restore_stop_policy()
        assert again._adopt_pending(7, plateau=now)
        assert _counts(again.stop_policy) == (1, 1) and again._plateau_step_at == expected


def test_the_hint_puts_no_full_stop_after_a_backtick_or_a_cut():
    from evolvekit.prompts import LedgerEntry

    quoted = LedgerEntry(id="g004-c0013", operator="big_step", change=None, outcome="failed: `boom`")
    assert big_step_reason(scheduled=True, every=3, stagnant=0, previous=quoted).endswith("failed: `boom`")
    long = LedgerEntry(id="g004-c0013", operator="big_step", change=None, outcome="raced out " * 40)
    assert big_step_reason(scheduled=True, every=3, stagnant=0, previous=long).endswith(long.line())
    assert long.line().endswith("...")


_NEVER_BETTER = [
    _WORST_FIT,
    _WORST_FIT.replace("[b - item for b in bins]", "[b - item + 1.0 for b in bins]"),
    _WORST_FIT.replace("[b - item for b in bins]", "[2.0 * (b - item) for b in bins]"),
    _WORST_FIT.replace("[b - item for b in bins]", "[b - 0.5 * item for b in bins]"),
]


def _spacing_driver(run_dir):
    return _driver(run_dir, responses=_NEVER_BETTER, big_step_every=3, generations=12,
                   children_per_generation=1, adaptive_children=None, operators={"rewrite": 1.0},
                   inspirations=0, stop={"patience": 13})[0]


def _big_step_generations(driver):
    return sorted(int(c.generation) for c in driver.archive if str(c.operator).startswith("big_step"))


def test_the_spacing_is_rebuilt_on_resume(tmp_path):
    # Plateau from stagnant 6 (patience 13): generation 7 on. Scheduled at 3, 6, 9, 12;
    # the plateau's own step at 7, then none at 8 (inside the window) nor at 10-11.
    whole = _spacing_driver(tmp_path / "whole")
    whole.run()
    assert _big_step_generations(whole) == [3, 6, 7, 9, 12]

    split = _spacing_driver(tmp_path / "split")
    split.run(generations=7)
    resumed = _spacing_driver(tmp_path / "split")
    resumed.run()  # generations 8-12: generation 8 is inside the window opened at 7
    assert _big_step_generations(resumed) == _big_step_generations(whole)
    assert resumed._plateau_step_at == whole._plateau_step_at == 12


def test_the_replay_reads_the_recorded_plateau(tmp_path):
    import json

    driver = _spacing_driver(tmp_path)
    driver.run(generations=7)
    events = tmp_path / "run" / "events.jsonl"
    lines = [json.loads(line) for line in events.read_text(encoding="utf-8").splitlines() if line.strip()]
    started = {e["generation"]: e["plateau"] for e in lines if e.get("type") == "generation_started"}
    assert started[7] is True and started[6] is False

    legacy = [{k: v for k, v in e.items() if k != "plateau"} for e in lines]
    events.write_text("".join(json.dumps(e) + "\n" for e in legacy), encoding="utf-8")
    again = _spacing_driver(tmp_path)
    again.resume()
    again._restore_stop_policy()
    assert again._plateau_step_at == 7, "a legacy run falls back to the stop policy's replay"

    flipped = [{**e, "plateau": False} if e.get("type") == "generation_started" else e for e in lines]
    events.write_text("".join(json.dumps(e) + "\n" for e in flipped), encoding="utf-8")
    third = _spacing_driver(tmp_path)
    third.resume()
    third._restore_stop_policy()
    assert third._plateau_step_at is None, "the recorded plateau wins over the replayed one"
