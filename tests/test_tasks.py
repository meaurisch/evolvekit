"""The entry points and the CI gate that calls them.

CI installed with `pip install -e ".[dev]" 2>/dev/null || pip install pytest`:
when the dev install failed it went on with pytest alone, and the lint step
failed with "No module named ruff" -- an error about the wrong thing. And an
unbounded `ruff>=0.6` let a new ruff release turn CI red with no code change.
"""

from __future__ import annotations

import re

import tasks
from tests.conftest import ROOT


def test_check_runs_the_linter_as_well_as_every_test(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(tasks, "lint", lambda: calls.append("lint") or 1)
    monkeypatch.setattr(tasks, "full", lambda: calls.append("full") or 0)
    assert tasks.check() != 0, "a lint failure fails the gate"
    assert calls == ["lint", "full"], "and the tests still run, so one push reports both"


def test_ci_installs_what_it_needs_or_fails_there():
    workflow = (ROOT / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8")
    installs = [line for line in workflow.splitlines() if "pip install" in line]
    assert installs and all("||" not in line and "2>/dev/null" not in line for line in installs), installs
    assert "python tasks.py check" in workflow


def test_ruff_is_pinned_below_the_next_minor_release():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'"ruff>=[\d.]+,<[\d.]+"', pyproject), "ruff needs an upper bound"


def test_agents_md_names_every_entry_point_tasks_py_has():
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    line = next(line for line in agents.splitlines() if "Entry points" in line)
    for name in ("test", "full", "run", "check", "lint"):
        assert name in line, name
