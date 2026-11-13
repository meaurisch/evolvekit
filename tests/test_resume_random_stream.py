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

import json
import sys
from dataclasses import replace

import pytest

from evolvekit.config import ArchiveConfig, build_config, load_config
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


SOLVER = (
    "import argparse, json\n"
    "p = argparse.ArgumentParser()\n"
    "p.add_argument('--x', type=float); p.add_argument('--y', type=float)\n"
    "a = p.parse_args()\n"
    "print(json.dumps({'cost': 100 + 40 * abs(a.x - 0.3) + 25 * abs(a.y - 0.7)}))\n"
)


def _typed_config(tmp_path, operators, generations):
    (tmp_path / "solver.py").write_text(SOLVER, encoding="utf-8", newline="\n")
    return build_config(
        {
            "problem": {"parameters": {"x": {"type": "float", "low": 0.0, "high": 1.0, "default": 0.5},
                                       "y": {"type": "float", "low": 0.0, "high": 1.0, "default": 0.5}}},
            "evaluate": {
                "stages": [
                    {"id": "static", "kind": "builtin-static"},
                    {"id": "full", "kind": "command", "kpis_from": "stdout",
                     "command": f'"{sys.executable}" solver.py {{params}}', "timeout": 60},
                ],
                "score": {"objective": "cost", "direction": "minimize"},
            },
            "search": {"operators": operators, "children_per_generation": 4, "generations": generations,
                       "seed": 4, "novelty": {"behavioural": "off"}},
            "stop": {"patience": 20},
        },
        base_dir=tmp_path,
    )


def _run(project, *, operators, generations):
    """A fresh run of `generations` rounds, of a study planned for 3."""
    project.mkdir(parents=True, exist_ok=True)
    driver = Driver(_typed_config(project, operators, 3), run_dir=project / "run", log=lambda m: None)
    driver.run(generations=generations)
    return driver.ledger.runs()


def _resume(project, *, operators, generations):
    """The same directory resumed until it holds `generations` rounds."""
    driver = Driver(_typed_config(project, operators, 3), run_dir=project / "run", log=lambda m: None)
    driver.run(generations=generations - max(int(r["generation"]) for r in driver.ledger.runs()))
    return driver.ledger.runs()


def _lhs_params(rows):
    return [r["params"] for r in rows if r["operator"] == "param_lhs"]


def test_a_resumed_lhs_run_proposes_what_the_uninterrupted_run_proposed(tmp_path):
    """Decision 8: the cube and its next index are on disk, so a run stopped after
    generation 1 and resumed hands out the rows of the same cube, in the same order,
    as one that never stopped. (The operators and parents a resumed session draws
    come from its own stream, `_random_stream`, so which children are `param_lhs`
    may differ; the rows they take do not.)"""
    mix = {"param_lhs": 0.5, "param_local": 0.5}
    whole = _run(tmp_path / "whole", operators=mix, generations=3)
    first = _run(tmp_path / "part", operators=mix, generations=1)
    part = _resume(tmp_path / "part", operators=mix, generations=3)
    cube = json.loads((tmp_path / "part" / "run" / "work" / "lhs.json").read_text(encoding="utf-8"))
    k = cube["cubes"]["typed"]["K"]
    assert k == 6  # generations 3 x children 4 x share 0.5
    before = len(_lhs_params(first))
    assert before >= 1, "the cube was built in generation 1, before the stop"
    shared = min(k, len(_lhs_params(whole)), len(_lhs_params(part)))
    assert shared > before, "the resumed session took rows of the first cube too"
    assert _lhs_params(whole)[:shared] == _lhs_params(part)[:shared]


def test_resuming_is_reproducible_too(sweepable_project, tmp_path):  # noqa: F811
    config = _config(sweepable_project)
    for name in ("one", "two"):
        _driver(config, tmp_path / name).run(generations=1)
        _driver(config, tmp_path / name).run(generations=1)
    one = _driver(config, tmp_path / "one")
    two = _driver(config, tmp_path / "two")
    assert _blocks(one, 2) == _blocks(two, 2)
