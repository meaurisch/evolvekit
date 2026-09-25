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

import re
import shutil

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
        r'command: "\{python\} evaluate\.py[^"]*"',
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


def test_run_does_not_exit_0_when_it_aborted(tmp_path, monkeypatch, capsys):
    """A wrapper script -- a cron job, a CI step, an agent -- reads the exit
    code. "The seed failed, nothing was searched" must not look like success."""
    from evolvekit.cli import EXIT_ABORTED, main

    work = tmp_path / "binpacking"
    shutil.copytree(EXAMPLE_DIR, work, ignore=shutil.ignore_patterns("__pycache__"))
    cfg_path = work / "evolvekit.yaml"
    assert main(["run", "--config", str(cfg_path), "--run-dir", str(tmp_path / "fine"), "--generations", "1", "--quiet"]) == 0

    text = cfg_path.read_text(encoding="utf-8")
    broken, n = re.subn(r'command: "\{python\} evaluate\.py[^"]*"', 'command: "{python} -c pass {candidate} {out}"', text, count=1)
    assert n == 1
    cfg_path.write_text(broken, encoding="utf-8")
    capsys.readouterr()
    assert main(["run", "--config", str(cfg_path), "--run-dir", str(tmp_path / "broken"), "--quiet"]) == EXIT_ABORTED == 4
    assert "seed failed evaluation" in capsys.readouterr().out


FLAKY_SOLVER = (
    "import argparse, json, pathlib, sys\n"
    "p = argparse.ArgumentParser(); p.add_argument('--x', type=float)\n"
    "a = p.parse_args()\n"
    "if pathlib.Path('broken').exists(): sys.exit('the harness is broken')\n"
    "print(json.dumps({'cost': 100 + abs(a.x - 0.3)}))\n"
)
FLAKY_RAW = {
    "problem": {"parameters": {"x": {"type": "float", "low": 0.0, "high": 1.0, "default": 0.9}}},
    "evaluate": {
        "stages": [
            {"id": "static", "kind": "builtin-static"},
            {"id": "full", "kind": "command", "kpis_from": "stdout", "command": "{python} solver.py {params}"},
        ],
        "score": {"objective": "cost", "direction": "minimize"},
    },
    "search": {"operators": {"param_lhs": 1.0}, "children_per_generation": 2, "generations": 2, "seed": 1},
    "budget": {"max_full_evals_per_day": 100},
}


@pytest.mark.slow
def test_running_an_aborted_run_again_does_not_breed_past_its_failed_seed(tmp_path):
    """The abort is not a state a second invocation may step over.

    A wrapper that retries on failure ran the same command again: the resume
    found the failed seed in `runs.jsonl`, skipped the seed check, bred
    against the broken harness and exited 0 -- with a model operator, paying
    for it. The seed is evaluated again first (the evaluator may have been
    fixed in between), and a seed that still fails aborts exactly as before.
    """
    import yaml

    from evolvekit.cli import EXIT_ABORTED, main

    (tmp_path / "solver.py").write_text(FLAKY_SOLVER, encoding="utf-8")
    (tmp_path / "broken").write_text("", encoding="utf-8")
    config_path = tmp_path / "evolvekit.yaml"
    config_path.write_text(yaml.safe_dump(FLAKY_RAW, sort_keys=False), encoding="utf-8")
    argv = ["run", "--config", str(config_path), "--run-dir", str(tmp_path / "run"), "--quiet"]

    assert main(argv) == EXIT_ABORTED
    assert main(argv) == EXIT_ABORTED, "the second invocation stepped over the failed seed"
    rows = [r for r in Ledger(tmp_path / "run").runs()]
    assert {r["operator"] for r in rows} == {"human-seed"}, "nothing was bred against a broken harness"

    (tmp_path / "broken").unlink()  # the harness is fixed: the same command now searches
    assert main(argv) == 0
    rows = Ledger(tmp_path / "run").runs()
    seeds = [r for r in rows if r["operator"] == "human-seed"]
    assert seeds[-1]["competes"] and not any(s["competes"] for s in seeds[:-1])
    assert max(int(r["generation"]) for r in rows) == 2
