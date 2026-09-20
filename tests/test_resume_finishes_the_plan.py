"""`search.generations` is the plan for the run directory, not for the session.

A twelve-generation run that died in generation 9 used to be "resumed" for
twelve more. With an evaluator that takes an hour and a half per generation,
that is the difference between finishing tonight and tomorrow night.
"""

from __future__ import annotations

import pytest

from evolvekit.config import build_config
from evolvekit.search.driver import Driver

SOLVER = "import argparse, json\np = argparse.ArgumentParser(); p.add_argument('--x', type=float)\nprint(json.dumps({'cost': 100 + abs(p.parse_args().x - 0.3)}))\n"


def _driver(tmp_path, generations: int) -> Driver:
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
            "search": {"operators": {"param_lhs": 1.0}, "children_per_generation": 2, "generations": generations, "seed": 1},
            "budget": {"max_full_evals_per_day": 100},
            "stop": {"patience": 50},
        },
        base_dir=tmp_path,
    )
    return Driver(config, run_dir=tmp_path / "run", log=lambda m: None)


@pytest.mark.slow
def test_a_resumed_run_finishes_its_plan_and_then_has_nothing_left_to_do(tmp_path):
    first = _driver(tmp_path, 4)
    first.run(generations=2)  # as if it had died after generation 2
    assert {r["generation"] for r in first.ledger.runs()} == {0, 1, 2}

    second = _driver(tmp_path, 4)
    summary = second.run()
    assert summary.generations == 2, "generations 3 and 4: what was left of the plan, not four more"
    assert {r["generation"] for r in second.ledger.runs()} == {0, 1, 2, 3, 4}

    third = _driver(tmp_path, 4)
    summary = third.run()
    assert summary.generations == 0
    assert summary.stop_reason.startswith("the run directory already holds its 4 planned generation(s)")
    assert "--generations K" in summary.stop_reason and not summary.aborted
    assert len(third.ledger.runs()) == len(second.ledger.runs())


@pytest.mark.slow
def test_an_explicit_count_still_means_that_many_more(tmp_path):
    _driver(tmp_path, 2).run()
    more = _driver(tmp_path, 2)
    assert more.run(generations=1).generations == 1
    assert {r["generation"] for r in more.ledger.runs()} == {0, 1, 2, 3}


@pytest.mark.slow
def test_the_event_log_says_how_far_the_plan_goes(tmp_path):
    from evolvekit.events import read_events

    _driver(tmp_path, 5).run(generations=2)
    _driver(tmp_path, 5).run()
    started = [e for e in read_events(tmp_path / "run") if e["type"] == "run_started"]
    assert [(e["first_generation"], e["generations_planned"]) for e in started] == [(1, 2), (3, 3)]
