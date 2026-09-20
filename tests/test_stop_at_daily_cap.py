"""`budget.max_full_evals_per_day` is a stop, not a slow leak.

Once the cap was reached the run carried on for every remaining generation:
it bred children (paying for them, with a model), ran their cheap stages, and
then skipped the one stage that counts -- "unfinished: 17" at the end of a run
that looked busy and searched nothing.
"""

from __future__ import annotations

import sys

import pytest

from evolvekit.config import build_config
from evolvekit.search.driver import Driver

SOLVER = "import argparse, json\np = argparse.ArgumentParser(); p.add_argument('--x', type=float)\nprint(json.dumps({'cost': 100 + abs(p.parse_args().x - 0.3)}))\n"


@pytest.mark.slow
def test_the_run_stops_when_no_candidate_can_reach_the_final_stage_any_more(tmp_path):
    (tmp_path / "solver.py").write_text(SOLVER, encoding="utf-8")
    config = build_config(
        {
            "problem": {"parameters": {"x": {"type": "float", "low": 0.0, "high": 1.0, "default": 0.9}}},
            "evaluate": {
                "stages": [
                    {"id": "static", "kind": "builtin-static"},
                    {"id": "full", "kind": "command", "kpis_from": "stdout", "command": "{python} solver.py {params}"},
                ],
                "score": {"objective": "cost", "direction": "minimize"},
            },
            "search": {"operators": {"param_lhs": 1.0}, "children_per_generation": 4, "generations": 6, "seed": 1},
            "budget": {"max_full_evals_per_day": 6},
            "stop": {"patience": 20},
        },
        base_dir=tmp_path,
    )
    driver = Driver(config, run_dir=tmp_path / "run", log=lambda m: None)
    summary = driver.run()
    assert summary.stop_reason.startswith("budget.max_full_evals_per_day reached: 6 of 6")
    assert "tomorrow" in summary.stop_reason and not summary.aborted
    assert summary.generations == 2, "the seed and generation 1 fit; generation 2 hits the cap; nothing is bred after that"
    skipped = [r for r in driver.ledger.runs() if "daily full-evaluation cap" in (r.get("last_failure") or "")]
    assert len(skipped) == 3, "only the rest of the generation in which the cap was reached"
