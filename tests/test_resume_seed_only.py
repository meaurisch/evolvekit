"""A run that was stopped before generation 1 finished must still resume.

Such a run directory holds exactly one row: the seed, in generation 0. `_run`
asked "was anything resumed?" by testing the last generation *number* for
truth, and 0 is falsy -- so the run was treated as new, a second seed was
bred and evaluated, the behaviour index (which had been restored) recognised it
as a twin of the first, and the run aborted with "seed failed evaluation -- fix
the harness before spending". On an expensive evaluator the first generation is
exactly where an interruption is most likely: it is where all the time goes.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from evolvekit.config import load_config
from evolvekit.search.driver import SEED_OPERATOR, Driver
from tests.conftest import EXAMPLE_CONFIG

pytestmark = pytest.mark.slow


def test_a_run_interrupted_before_its_first_generation_finished_resumes(tmp_path):
    config = load_config(EXAMPLE_CONFIG)
    config = replace(config, search=replace(config.search, scratchpad_every=0))
    run_dir = tmp_path / "run"

    # `--generations 0` evaluates the seed and stops: the same directory a run
    # killed during generation 1 leaves behind.
    first = Driver(config, run_dir=run_dir).run(generations=0)
    assert first.seed_score == pytest.approx(-5.871, abs=0.01)

    messages: list[str] = []
    second = Driver(config, run_dir=run_dir, log=messages.append)
    summary = second.run(generations=1)

    assert "seed failed" not in summary.stop_reason, summary.stop_reason
    assert not any(m.startswith("ABORT") for m in messages), messages
    rows = second.ledger.runs()
    assert [r["operator"] for r in rows].count(SEED_OPERATOR) == 1
    assert summary.seed_score == pytest.approx(-5.871, abs=0.01)
    assert summary.generations == 1
    assert max(int(r["generation"]) for r in rows) == 1
