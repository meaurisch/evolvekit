"""Guards learned from the first real run (2026-08-22).

That run spent $0.52 on twelve LLM calls while every candidate — the seed
included — scored `failure_score`: the run dir was relative, evaluator
subprocesses run with cwd = the config's base_dir, so every candidate path
they were handed dangled. Two rules fall out of it:

1. The run dir is absolute from the moment a Ledger exists.
2. If the seed cannot get through the cascade, the harness is broken and the
   driver stops before the first LLM call.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

import pytest

from evolvekit.config import load_config
from evolvekit.ledger import Ledger
from evolvekit.search.driver import Driver
from tests.conftest import EXAMPLE_CONFIG, EXAMPLE_DIR


def test_ledger_resolves_a_relative_run_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ledger = Ledger("runs/rel")
    assert ledger.run_dir.is_absolute()
    assert ledger.run_dir == (tmp_path / "runs" / "rel").resolve()


@pytest.mark.slow
def test_a_relative_run_dir_from_a_foreign_cwd_still_evaluates(tmp_path, monkeypatch):
    """The config lives in examples/binpacking; we run from somewhere else."""
    monkeypatch.chdir(tmp_path)
    config = load_config(EXAMPLE_CONFIG)
    driver = Driver(config, run_dir="runs/foreign", log=lambda _m: None)
    summary = driver.run(generations=1)
    assert summary.seed_score == pytest.approx(-5.871, abs=0.01)
    assert "seed failed" not in summary.stop_reason
    rows = driver.ledger.runs()
    assert all(row["score"] != config.evaluate.failure_score for row in rows[:1])
    assert (tmp_path / "runs" / "foreign" / "runs.jsonl").exists()


def test_a_broken_evaluator_aborts_at_the_seed_with_zero_spend(tmp_path, monkeypatch):
    work = tmp_path / "binpacking"
    shutil.copytree(EXAMPLE_DIR, work, ignore=shutil.ignore_patterns("__pycache__"))
    cfg_path = work / "evolvekit.yaml"
    text = cfg_path.read_text(encoding="utf-8")
    broken, n = re.subn(
        r'command: "python evaluate\.py[^"]*"',
        'command: "python -c \\"import sys; sys.exit(1)\\" {candidate} {out}"',
        text,
        count=1,  # only the proxy stage; the full stage is never reached
    )
    assert n == 1
    cfg_path.write_text(broken, encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    config = load_config(cfg_path)
    messages: list[str] = []
    driver = Driver(config, run_dir="runs/broken", log=messages.append)
    summary = driver.run()

    assert summary.stop_reason.startswith("seed failed evaluation")
    assert summary.seed_score == config.evaluate.failure_score
    assert summary.generations == 0
    assert any(m.startswith("ABORT") for m in messages)
    # Nothing was ever asked of a model.
    assert driver.ledger.totals()["usd"] == 0
    assert not driver.ledger.usage_path.exists() or driver.ledger.usage_path.stat().st_size == 0
    rows = driver.ledger.runs()
    assert len(rows) == 1 and rows[0]["operator"] == "human-seed"
