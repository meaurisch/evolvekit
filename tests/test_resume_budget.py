"""The caps belong to the run directory, not to the process.

`budget.max_usd` is documented as a hard stop and resuming as the way to carry
on after a halt. Together those two promises only hold if the guard knows what
the directory has already spent. It did not: every invocation started from
zero, so re-running the same command on a budget-stopped run bought the whole
allowance again -- $0.0524 against a $0.01 cap after three invocations, in the
session that found this. `stop.patience` and `max_full_evals_per_day` reset the
same way.

Driver-level, so `slow`.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from evolvekit.config import load_config
from evolvekit.providers.fake import FakeProvider
from evolvekit.search.driver import Driver
from tests.conftest import EXAMPLE_CONFIG

pytestmark = pytest.mark.slow

STUCK = "```python\ndef priority(item, bins):\n    return [float(-(b - item)) for b in bins]\n```"
"""A child that is always the same program: nothing can ever improve."""


def _config(**budget):
    config = load_config(EXAMPLE_CONFIG)
    return replace(
        config,
        budget=replace(config.budget, **budget),
        search=replace(config.search, scratchpad_every=0),
    )


def test_a_run_stopped_by_max_usd_stays_stopped_when_it_is_run_again(tmp_path):
    config = _config(max_usd=0.002)
    first = Driver(config, run_dir=tmp_path / "run").run()
    assert "max_usd" in first.stop_reason
    spent = first.totals["usd"]
    calls = first.totals["calls"]
    assert spent >= 0.002

    second = Driver(config, run_dir=tmp_path / "run").run()
    assert "max_usd" in second.stop_reason
    assert second.generations == 0, "a generation ran on a budget already spent"
    assert second.totals["calls"] == calls
    assert second.totals["usd"] == pytest.approx(spent)


def test_a_run_stopped_by_max_tokens_stays_stopped_when_it_is_run_again(tmp_path):
    config = _config(max_tokens=3000)
    first = Driver(config, run_dir=tmp_path / "run").run()
    assert "max_tokens" in first.stop_reason

    second = Driver(config, run_dir=tmp_path / "run").run()
    assert "max_tokens" in second.stop_reason
    assert second.generations == 0
    assert second.totals["calls"] == first.totals["calls"]


def test_the_guard_reports_what_the_directory_spent_not_what_this_process_did(tmp_path):
    config = _config()
    first = Driver(config, run_dir=tmp_path / "run")
    first.run(generations=1)

    second = Driver(config, run_dir=tmp_path / "run")
    second.resume()
    assert second.budget.state.usd == pytest.approx(first.budget.state.usd)
    assert second.budget.state.tokens == first.budget.state.tokens
    assert second.budget.state.calls == first.budget.state.calls
    assert first.budget.state.usd > 0


def test_full_evaluations_already_run_today_count_against_the_daily_cap(tmp_path):
    config = _config()
    first = Driver(config, run_dir=tmp_path / "run")
    first.run(generations=1)
    today = datetime.now(timezone.utc).date().isoformat()
    used = first.budget.full_evals_today(today)
    assert used >= 1

    second = Driver(config, run_dir=tmp_path / "run")
    second.resume()
    assert second.budget.full_evals_today(today) == used


def test_patience_already_used_up_is_still_used_up_after_a_resume(tmp_path):
    config = _config()
    config = replace(
        config,
        stop=replace(config.stop, patience=2, min_big_steps=0),
        search=replace(config.search, generations=6, scratchpad_every=0),
    )

    def driver():
        provider = FakeProvider([STUCK])
        return Driver(
            config,
            run_dir=tmp_path / "run",
            providers={"small": provider, "strong": provider},
        )

    first = driver().run()
    assert "stop.patience" in first.stop_reason
    flat_generations = first.generations

    second = driver().run()
    assert "stop.patience" in second.stop_reason
    assert second.generations == 0, (
        f"the first run stopped after {flat_generations} flat generation(s); "
        "the second one forgot them and ran more"
    )
