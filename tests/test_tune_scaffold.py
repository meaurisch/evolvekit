"""`init --template tune` and `examples/cli-solver`: a new user's first ten
minutes. Both have to run exactly as they are written down."""

from __future__ import annotations

from pathlib import Path

import pytest

from evolvekit.cli import main
from evolvekit.config import load_config
from evolvekit.preflight import preflight

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "cli-solver"


def test_the_tune_scaffold_is_complete_and_says_what_to_do_next(tmp_path, capsys):
    assert main(["init", str(tmp_path / "mine"), "--template", "tune"]) == 0
    printed = capsys.readouterr().out
    written = sorted(p.relative_to(tmp_path / "mine").as_posix() for p in (tmp_path / "mine").rglob("*") if p.is_file())
    assert written == ["evolvekit.yaml", "instances/large.json", "instances/medium.json", "instances/small.json", "solver.py"]
    for step in ("preflight --config", "run --config", "confirm --config", "--seeds 1001,1002,1003"):
        assert step in printed

    config = load_config(tmp_path / "mine" / "evolvekit.yaml")
    assert config.models is None and not config.search.uses_llm, "no model, no key"
    assert config.problem.parameters.names == ["steps", "cooling", "greedy"]
    assert config.final_stage.instance_names() == ("large", "medium", "small")


def test_the_tune_scaffold_does_not_overwrite_what_is_there(tmp_path, capsys):
    (tmp_path / "solver.py").write_text("mine\n", encoding="utf-8")
    assert main(["init", str(tmp_path), "--template", "tune"]) == 0
    assert (tmp_path / "solver.py").read_text(encoding="utf-8") == "mine\n"
    assert "exists, not overwritten" in capsys.readouterr().out


@pytest.mark.slow
def test_the_tune_scaffold_passes_preflight_as_written(tmp_path):
    main(["init", str(tmp_path), "--template", "tune"])
    report = preflight(load_config(tmp_path / "evolvekit.yaml"))
    assert report.failures == [] and report.stages[-1].runs == 6
    assert report.stages[-1].kpis["cost"] > 0


@pytest.mark.slow
def test_the_scaffold_runs_is_confirmed_and_exported_with_the_commands_it_prints(tmp_path, capsys):
    main(["init", str(tmp_path), "--template", "tune"])
    config, run_dir = str(tmp_path / "evolvekit.yaml"), str(tmp_path / "runs" / "first")
    assert main(["run", "--config", config, "--run-dir", run_dir, "--generations", "2", "--quiet"]) == 0
    # `run` exits 0 even when it aborts at the seed, so ask the run directory.
    from evolvekit.status import build_status

    document = build_status(run_dir)
    assert document["health"]["state"] == "finished" and document["health"]["stop_reason"] == "generations exhausted"
    assert document["progress"]["baseline"]["objective"] == pytest.approx(100.0)
    capsys.readouterr()
    assert main(["confirm", "--config", config, "--run-dir", run_dir, "--seeds", "1001,1002"]) in (0, 1)
    assert "## `g00" in capsys.readouterr().out, "a comparison was printed, not an error"
    assert main(["export", "--run-dir", run_dir, "--format", "flags", "--config", config]) == 0
    assert "--steps" in capsys.readouterr().out


@pytest.mark.slow
def test_the_cli_solver_example_passes_preflight_as_written(monkeypatch):
    monkeypatch.setenv("CLI_SOLVER_NO_CRASH", "1")  # its one-in-25 crash is real randomness
    config = load_config(EXAMPLE / "evolvekit.yaml")
    assert config.models is None
    assert config.final_stage.instance_names() == ("a-40", "b-60", "c-80", "d-110", "e-150")
    report = preflight(config)
    assert report.failures == [], report.failures
    assert [s.stage_id for s in report.stages] == ["static", "screen", "full"]
