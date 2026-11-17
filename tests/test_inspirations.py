"""Model-free operators draw no inspirations (A2 decision 19).

An inspiration is an elite from another cell, shown to a model in its prompt.
The `param_*` operators call no model and never read one, but every child used
to draw them from the grid -- and once a typed space's parameter axes spread
the archive over several cells, those draws shifted every later number the run
took from its generator. The same tuning run then proposed different
configurations only because the grid had more cells.
"""

from __future__ import annotations

import random
import sys

from evolvekit.config import build_config
from evolvekit.search.driver import Driver

SOLVER = (
    "import argparse, json\n"
    "p = argparse.ArgumentParser()\n"
    "p.add_argument('--x', type=float); p.add_argument('--y', type=float)\n"
    "a = p.parse_args()\n"
    "print(json.dumps({'cost': 100 + 40 * abs(a.x - 0.3) + 25 * abs(a.y - 0.7)}))\n"
)
MIX = {"param_lhs": 0.3, "param_local": 0.4, "param_tpe": 0.2, "param_cross": 0.1}


def _driver(tmp_path, archive):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "solver.py").write_text(SOLVER, encoding="utf-8", newline="\n")
    search = {"operators": MIX, "children_per_generation": 4, "generations": 3,
              "seed": 7, "novelty": {"behavioural": "off"}}
    if archive is not None:
        search["archive"] = archive
    config = build_config(
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
            "search": search,
            "stop": {"patience": 20},
        },
        base_dir=tmp_path,
    )
    return Driver(config, run_dir=tmp_path / "run", log=lambda m: None)


def _proposals(driver):
    return [(r["generation"], r["operator"], r["params"]) for r in driver.ledger.runs()]


def test_parameter_axes_do_not_change_what_a_tuning_run_proposes(tmp_path):
    one_cell = _driver(tmp_path / "one", {"descriptors": [{"kpi": "complexity"}]})
    one_cell.run()
    grid = _driver(tmp_path / "grid", None)  # the default: x and y, 4 bins each
    grid.run()
    assert len(one_cell.grid.cells) == 1
    assert len(grid.grid.cells) > 1, "the parameter axes must spread the archive for this to mean anything"
    assert _proposals(grid) == _proposals(one_cell)
    assert not any(r["inspiration_ids"] for r in grid.ledger.runs())


def test_a_model_operator_still_draws_inspirations_and_a_model_free_one_only_its_count(tmp_path):
    driver = _driver(tmp_path, None)
    driver.run()
    parent = driver.grid.elites()[0]
    budget = driver.config.search.inspirations

    driver.rng = random.Random(11)
    assert driver._inspirations(parent, "diff"), "a model operator is shown elites of other cells"

    driver.rng = random.Random(11)
    assert driver._inspirations(parent, "param_local") == []
    after = driver.rng.random()
    expected = random.Random(11)
    expected.randint(1, budget)  # how many it would have shown: still drawn, the stream stays in step
    assert after == expected.random()
