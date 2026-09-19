"""A resumed run must not replay the random stream it already used.

`Driver` seeds its generator from `search.seed`, which is what makes a fresh run
reproducible. A *resumed* run was seeded the same way, so it drew the same
operators, the same parents and -- what hurts -- the same `param_lhs` sweep
seeds as its first generation had. `param_lhs` samples the declared ranges from
that seed alone, so every child of the resumed generation was a block the run
already held, and the novelty filter threw it away: 89 of 100 children in one
observed sweep, 20 of 20 in the next.

It takes the default archive to see it. With one `complexity` axis every
constants-only candidate lands in the same cell, so there are no inspirations
to draw and the stream stays in step; a second, behavioural axis shifts the
stream and hides the replay -- which is why the fixture's own archive is
swapped for the default below.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from evolvekit.config import ArchiveConfig, load_config
from evolvekit.providers.fake import FakeProvider
from evolvekit.search.driver import Driver
from tests.test_params import sweepable_project  # noqa: F401  (a fixture)

pytestmark = pytest.mark.slow


def _driver(config, run_dir):
    provider = FakeProvider(["never called"])
    return Driver(config, run_dir=run_dir, providers={"small": provider, "strong": provider})


def _config(project):
    config = load_config(project)
    return replace(config, search=replace(config.search, archive=ArchiveConfig()))


def _blocks(driver, generation):
    return [r["block"] for r in driver.ledger.runs() if int(r["generation"]) == generation]


def test_a_resumed_sweep_draws_candidates_it_has_not_drawn_before(
    sweepable_project, tmp_path  # noqa: F811
):
    config = _config(sweepable_project)
    first = _driver(config, tmp_path / "run")
    first.run(generations=1)
    assert len(_blocks(first, 1)) == 3

    second = _driver(config, tmp_path / "run")
    second.run(generations=1)
    resumed = [r for r in second.ledger.runs() if int(r["generation"]) == 2]
    assert len(resumed) == 3
    replayed = [r["id"] for r in resumed if r["novelty"] in ("duplicate", "no_op")]
    assert not replayed, (
        f"{len(replayed)} of {len(resumed)} children of the resumed generation were "
        "blocks the run already held"
    )


def test_a_fresh_run_is_still_reproducible_from_its_seed(sweepable_project, tmp_path):  # noqa: F811
    config = _config(sweepable_project)
    one = _driver(config, tmp_path / "one")
    one.run(generations=2)
    two = _driver(config, tmp_path / "two")
    two.run(generations=2)
    assert _blocks(one, 1) == _blocks(two, 1)
    assert _blocks(one, 2) == _blocks(two, 2)


def test_resuming_is_reproducible_too(sweepable_project, tmp_path):  # noqa: F811
    config = _config(sweepable_project)
    for name in ("one", "two"):
        _driver(config, tmp_path / name).run(generations=1)
        _driver(config, tmp_path / name).run(generations=1)
    one = _driver(config, tmp_path / "one")
    two = _driver(config, tmp_path / "two")
    assert _blocks(one, 2) == _blocks(two, 2)
