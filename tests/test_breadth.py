"""Adaptive breadth: the run decides how many children a generation gets.

TurboEvolve's K-adaptation, reduced to the smallest rule that still has the
property that matters: grow on stagnation, give it back once the search is
climbing. Off unless configured.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from evolvekit.config import (
    AdaptiveChildrenConfig,
    ConfigError,
    build_config,
    load_config,
)
from evolvekit.providers.fake import FakeProvider
from evolvekit.search.breadth import AdaptiveBreadth
from evolvekit.search.driver import Driver
from tests.conftest import EXAMPLE_CONFIG

RULE = AdaptiveChildrenConfig(min=2, max=5, grow_after=2, shrink_after=1)


def _breadth(start=3, rule=RULE) -> AdaptiveBreadth:
    return AdaptiveBreadth(config=rule, current=start)


# -- the rule --------------------------------------------------------------


def test_two_flat_generations_buy_one_more_child():
    breadth = _breadth()
    assert breadth.update(improved=False).moved is False
    change = breadth.update(improved=False)
    assert change.direction == "grow"
    assert (change.previous, change.current) == (3, 4)
    assert "without improvement" in change.reason


def test_the_grow_counter_resets_after_it_fires():
    breadth = _breadth()
    breadth.update(False)
    breadth.update(False)  # grows to 4
    assert breadth.update(False).moved is False, "one flat generation is not two"
    assert breadth.update(False).current == 5


def test_growth_stops_at_the_ceiling():
    breadth = _breadth(start=5)
    for _ in range(8):
        change = breadth.update(False)
        assert change.current == 5
    assert "ceiling" in change.reason


def test_an_improvement_gives_a_child_back():
    breadth = _breadth(start=4)
    change = breadth.update(improved=True)
    assert change.direction == "shrink"
    assert (change.previous, change.current) == (4, 3)


def test_shrinking_stops_at_the_floor():
    breadth = _breadth(start=2)
    change = breadth.update(True)
    assert change.current == 2 and change.direction == "hold"
    assert "floor" in change.reason


def test_a_slower_shrink_needs_consecutive_improvements():
    rule = replace(RULE, shrink_after=3)
    breadth = _breadth(start=5, rule=rule)
    assert breadth.update(True).moved is False
    assert breadth.update(True).moved is False
    assert breadth.update(True).current == 4


def test_a_flat_generation_resets_the_shrink_counter():
    rule = replace(RULE, shrink_after=2)
    breadth = _breadth(start=5, rule=rule)
    breadth.update(True)
    breadth.update(False)   # resets the improving streak
    assert breadth.update(True).moved is False, "the streak started over"


def test_an_improvement_resets_the_grow_counter():
    breadth = _breadth()
    breadth.update(False)
    breadth.update(True)    # shrinks 3 -> 2, and clears the stagnation
    assert breadth.current == 2
    assert breadth.update(False).moved is False


def test_the_change_reads_as_one_log_line():
    breadth = _breadth()
    breadth.update(False)
    text = str(breadth.update(False))
    assert text.startswith("breadth 3 -> 4 (grow:")
    assert str(breadth.update(True)).startswith("breadth 4 -> 3 (shrink:")


def test_a_held_breadth_still_says_where_it_is_going():
    text = str(_breadth().update(False))
    assert text == "breadth 3 held (flat, 1 of 2 toward a grow)"


# -- construction ----------------------------------------------------------


def test_the_feature_is_off_unless_it_is_configured():
    config = load_config(EXAMPLE_CONFIG)
    off = replace(config.search, adaptive_children=None)
    assert AdaptiveBreadth.from_config(off) is None


def test_the_start_is_the_configured_breadth_clamped_into_range():
    config = load_config(EXAMPLE_CONFIG)
    search = replace(config.search, adaptive_children=RULE)
    assert AdaptiveBreadth.from_config(replace(search, children_per_generation=1)).current == 2
    assert AdaptiveBreadth.from_config(replace(search, children_per_generation=9)).current == 5
    assert AdaptiveBreadth.from_config(replace(search, children_per_generation=3)).current == 3


# -- config ----------------------------------------------------------------


def test_adaptive_children_is_absent_by_default(minimal_raw, tmp_path):
    assert build_config(minimal_raw, base_dir=tmp_path).search.adaptive_children is None


def test_min_and_max_are_required(minimal_raw, tmp_path):
    minimal_raw["search"] = {"adaptive_children": {"max": 6}}
    with pytest.raises(ConfigError, match="search.adaptive_children.min"):
        build_config(minimal_raw, base_dir=tmp_path)


def test_max_below_min_is_rejected(minimal_raw, tmp_path):
    minimal_raw["search"] = {"adaptive_children": {"min": 6, "max": 3}}
    with pytest.raises(ConfigError, match="max must be >= min"):
        build_config(minimal_raw, base_dir=tmp_path)


def test_unknown_keys_are_rejected(minimal_raw, tmp_path):
    minimal_raw["search"] = {
        "adaptive_children": {"min": 1, "max": 2, "grow_every": 3}
    }
    with pytest.raises(ConfigError, match="search.adaptive_children: unknown key"):
        build_config(minimal_raw, base_dir=tmp_path)


def test_the_counters_default_to_two_and_one(minimal_raw, tmp_path):
    minimal_raw["search"] = {"adaptive_children": {"min": 2, "max": 5}}
    rule = build_config(minimal_raw, base_dir=tmp_path).search.adaptive_children
    assert (rule.grow_after, rule.shrink_after) == (2, 1)


def test_the_bin_packing_example_turns_it_on():
    rule = load_config(EXAMPLE_CONFIG).search.adaptive_children
    assert rule == AdaptiveChildrenConfig(min=3, max=6, grow_after=2, shrink_after=1)
    assert load_config(EXAMPLE_CONFIG).stop.patience == 8


# -- inside the loop -------------------------------------------------------


FLAT = (
    "```python\n"
    "def priority(item, bins):\n"
    '    """{tag}"""\n'
    "    return [-(b - item) + {n} for b in bins]\n"
    "```"
)


@pytest.mark.slow
def test_a_stagnant_run_widens_its_generations(tmp_path):
    """Three flat generations at `grow_after: 1` must widen twice."""
    config = load_config(EXAMPLE_CONFIG)
    config = replace(
        config,
        # Static + proxy: the static stage alone carries no score, and a
        # seed with no score aborts the run before it can breed anything.
        evaluate=replace(config.evaluate, stages=config.evaluate.stages[:2]),
        stop=replace(config.stop, patience=99),
        search=replace(
            config.search,
            generations=3,
            children_per_generation=1,
            big_step_every=99,
            scratchpad_every=0,
            inspirations=0,
            operators={"rewrite": 1.0},
            adaptive_children=AdaptiveChildrenConfig(
                min=1, max=3, grow_after=1, shrink_after=1
            ),
        ),
    )
    provider = FakeProvider(
        [FLAT.format(tag=f"variant {n}", n=n) for n in range(1, 40)], cycle=True
    )
    lines: list[str] = []
    driver = Driver(
        config,
        run_dir=tmp_path / "breadth",
        providers={"small": provider, "strong": provider},
        log=lines.append,
    )
    summary = driver.run()

    # Every response is best fit plus a constant -- the same packing, so the
    # behaviour signature refuses them all and no generation ever improves:
    # 1 child, then 2, then 3.
    per_generation = {}
    for candidate in driver.archive:
        if candidate.generation:
            per_generation[candidate.generation] = per_generation.get(
                candidate.generation, 0
            ) + 1
    assert per_generation == {1: 1, 2: 2, 3: 3}
    assert summary.children_per_generation == 3
    assert any("breadth 1 -> 2 (grow" in line for line in lines)
    assert any("breadth 2 -> 3 (grow" in line for line in lines)


@pytest.mark.slow
def test_without_the_rule_the_breadth_never_moves(tmp_path):
    config = load_config(EXAMPLE_CONFIG)
    config = replace(
        config,
        # Static + proxy: the static stage alone carries no score, and a
        # seed with no score aborts the run before it can breed anything.
        evaluate=replace(config.evaluate, stages=config.evaluate.stages[:2]),
        stop=replace(config.stop, patience=99),
        search=replace(
            config.search,
            generations=2,
            children_per_generation=2,
            big_step_every=99,
            scratchpad_every=0,
            inspirations=0,
            operators={"rewrite": 1.0},
            adaptive_children=None,
        ),
    )
    provider = FakeProvider(
        [FLAT.format(tag=f"variant {n}", n=n) for n in range(1, 40)], cycle=True
    )
    lines: list[str] = []
    driver = Driver(
        config,
        run_dir=tmp_path / "fixed",
        providers={"small": provider, "strong": provider},
        log=lines.append,
    )
    summary = driver.run()
    assert driver.breadth is None
    assert summary.children_per_generation == 2
    assert not any("breadth" in line for line in lines)
