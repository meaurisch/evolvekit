"""D6, the sweep of small lies: each a promise the code did not keep."""

from __future__ import annotations

from dataclasses import replace

from evolvekit.budget import StopPolicy
from evolvekit.config import StopConfig, load_config
from evolvekit.prompts import OPERATOR_INSTRUCTIONS, big_step_reason
from evolvekit.providers.fake import FakeProvider
from evolvekit.search.driver import Driver
from tests.conftest import EXAMPLE_CONFIG


def _driver(tmp_path, responses=("no code here",), **search):
    config = load_config(EXAMPLE_CONFIG)
    config = replace(config, search=replace(config.search, scratchpad_every=0, **search))
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
