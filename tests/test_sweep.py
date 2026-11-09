"""D6, the sweep of small lies: each a promise the code did not keep."""

from __future__ import annotations

from dataclasses import replace

from evolvekit.config import load_config
from evolvekit.providers.fake import FakeProvider
from evolvekit.search.driver import Driver
from tests.conftest import EXAMPLE_CONFIG


def _driver(tmp_path, **search):
    config = load_config(EXAMPLE_CONFIG)
    config = replace(config, search=replace(config.search, scratchpad_every=0, **search))
    provider = FakeProvider(["no code here"], cycle=True)
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
