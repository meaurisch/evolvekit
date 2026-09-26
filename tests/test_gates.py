"""`evaluate.gates`: a KPI condition every candidate has to meet to compete.

A candidate that breaks a gate keeps its score for the record, is not promoted
and never competes; it is "not competing" with its reason, never an evaluation
failure. A seed that breaks one aborts the run before anything is searched.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evolvekit import cli
from evolvekit.candidate import Candidate, splice_block
from evolvekit.config import ConfigError, GateConfig, build_config
from evolvekit.evaluate.cascade import Cascade
from evolvekit.preflight import preflight
from evolvekit.search.driver import Driver

SOLVER = (
    "import argparse, json\n"
    "p = argparse.ArgumentParser(); p.add_argument('--x', type=float)\n"
    "a = p.parse_args()\n"
    "print(json.dumps({'cost': 100 + abs(a.x - 0.3) * 10, 'missed': 1 if a.x > 0.7 else 0}))\n"
)


def _raw(gates, *, default=0.5, stages=None, **search):
    return {
        "problem": {"parameters": {"x": {"type": "float", "low": 0.0, "high": 1.0, "default": default}}},
        "evaluate": {
            "stages": stages
            or [
                {"id": "static", "kind": "builtin-static"},
                {"id": "full", "kind": "command", "kpis_from": "stdout", "command": "{python} solver.py {params}"},
            ],
            "score": {"objective": "cost", "direction": "minimize"},
            "gates": gates,
        },
        "search": {"operators": {"param_lhs": 1.0}, "children_per_generation": 6, "generations": 2, "seed": 3, **search},
        "stop": {"patience": 20},
    }


def _config(tmp_path: Path, gates, **kwargs):
    (tmp_path / "solver.py").write_text(SOLVER, encoding="utf-8")
    return build_config(_raw(gates, **kwargs), base_dir=tmp_path)


def _candidate(config, cid: str, x: float) -> Candidate:
    block = config.problem.parameters.render_block({"x": x})
    source = splice_block(config.problem.skeleton_source(), block, config.problem.block_start, config.problem.block_end)
    return Candidate(id=cid, generation=1, block=block, source=source, operator="param_lhs")


# -- config -----------------------------------------------------------------


def test_a_gate_is_a_kpi_and_one_bound(tmp_path):
    config = _config(tmp_path, [{"kpi": "missed", "max": 0}, {"kpi": "on_time", "min": 0.95}])
    assert config.evaluate.gates == (GateConfig(kpi="missed", max=0.0), GateConfig(kpi="on_time", min=0.95))
    assert config.evaluate.required_kpis == ("cost", "missed", "on_time")


@pytest.mark.parametrize(
    "gate, message",
    [
        ({"kpi": "missed"}, r"evaluate\.gates\[0\]: give exactly one of `max` or `min`"),
        ({"kpi": "missed", "max": 0, "min": -1}, r"evaluate\.gates\[0\]: give exactly one of `max` or `min`"),
        ({"max": 0}, r"evaluate\.gates\[0\]\.kpi: required"),
        ({"kpi": "missed", "max": "zero"}, r"evaluate\.gates\[0\]\.max: expected a number"),
        ({"kpi": "missed", "max": 0, "why": "x"}, r"evaluate\.gates\[0\]: unknown key"),
    ],
)
def test_a_gate_that_cannot_work_is_a_config_error(tmp_path, gate, message):
    with pytest.raises(ConfigError, match=message):
        _config(tmp_path, [gate])


def test_a_gate_says_what_it_saw():
    assert GateConfig(kpi="missed", max=0.0).broken_by({"missed": 2.0}) == "missed = 2 > 0"
    assert GateConfig(kpi="on_time", min=0.95).broken_by({"on_time": 0.9}) == "on_time = 0.9 < 0.95"
    assert GateConfig(kpi="missed", max=0.0).broken_by({"missed": 0.0}) is None


# -- the cascade --------------------------------------------------------------


def test_a_run_that_does_not_report_a_gate_kpi_fails_and_says_which(tmp_path):
    config = _config(tmp_path, [{"kpi": "late", "max": 0}])
    result = Cascade(config, work_dir=tmp_path / "work").evaluate_generation([_candidate(config, "g001-c0001", 0.2)])[
        "g001-c0001"
    ]
    assert not result.competes
    assert "reported no 'late'" in (result.last_failure or "")


def test_a_candidate_that_breaks_a_gate_keeps_its_score_is_not_promoted_and_does_not_compete(tmp_path):
    stages = [
        {"id": "static", "kind": "builtin-static"},
        {"id": "screen", "kind": "command", "kpis_from": "stdout", "command": "{python} solver.py {params}",
         "promote": {"top_k_per_generation": 5}},
        {"id": "full", "kind": "command", "kpis_from": "stdout", "command": "{python} solver.py {params}"},
    ]
    config = _config(tmp_path, [{"kpi": "missed", "max": 0}], stages=stages)
    results = Cascade(config, work_dir=tmp_path / "work").evaluate_generation(
        [_candidate(config, "g001-c0001", 0.9), _candidate(config, "g001-c0002", 0.4)]
    )
    gated, fine = results["g001-c0001"], results["g001-c0002"]
    assert gated.gated == "missed = 1 > 0"
    assert gated.stages_reached == ["static", "screen"], "not promoted past the stage that gated it"
    assert gated.score == pytest.approx(-106.0) and not gated.last_failure and not gated.rejected
    assert not gated.competes
    assert fine.gated is None and fine.competes and fine.stages_reached == ["static", "screen", "full"]


# -- the driver --------------------------------------------------------------


@pytest.mark.slow
def test_gated_candidates_are_recorded_but_never_the_best(tmp_path):
    config = _config(tmp_path, [{"kpi": "missed", "max": 0}])
    driver = Driver(config, run_dir=tmp_path / "run")
    summary = driver.run()
    rows = driver.ledger.runs()
    gated = [r for r in rows if r.get("gated")]
    assert gated, "param_lhs over [0, 1] proposes x > 0.7 a third of the time"
    for row in rows:
        if row["params"] and row["params"]["x"] > 0.7:
            assert row["gated"] == "missed = 1 > 0" and row["competes"] is False and not row["last_failure"]
    assert summary.best is not None and summary.best.params["x"] <= 0.7


def test_the_search_learns_a_gated_configuration_is_a_bad_one(tmp_path):
    config = _config(tmp_path, [{"kpi": "missed", "max": 0}])
    driver = Driver(config, run_dir=tmp_path / "run")
    fine = Candidate(id="a", generation=1, block="", source="", operator="param_lhs", params={"x": 0.3},
                     score=-100.0, ranking_score=-100.0, competes=True)
    gated = Candidate(id="b", generation=1, block="", source="", operator="param_lhs", params={"x": 0.8},
                      score=-50.0, competes=False, gated="missed = 1 > 0")
    driver.archive = [fine, gated]
    observations = {o.values["x"]: o.fitness for o in driver._observations()}
    assert observations == {0.3: -100.0, 0.8: -101.0}, "below the worst finished one, like a failure"


@pytest.mark.slow
def test_a_seed_that_breaks_a_gate_aborts_the_run_with_the_gate_and_the_value(tmp_path):
    config = _config(tmp_path, [{"kpi": "missed", "max": 0}], default=0.9)
    summary = Driver(config, run_dir=tmp_path / "run").run()
    assert summary.aborted and summary.generations == 0
    assert summary.stop_reason.startswith("the seed breaks a gate: missed = 1 > 0")


@pytest.mark.slow
def test_run_exits_4_when_the_seed_breaks_a_gate(tmp_path):
    (tmp_path / "solver.py").write_text(SOLVER, encoding="utf-8")
    config_path = tmp_path / "evolvekit.yaml"
    config_path.write_text(json.dumps(_raw([{"kpi": "missed", "max": 0}], default=0.9)), encoding="utf-8")
    assert cli.main(["run", "--config", str(config_path), "--run-dir", str(tmp_path / "run"), "--quiet"]) == 4


def test_preflight_reports_a_seed_that_breaks_a_gate_as_a_failure(tmp_path):
    report = preflight(_config(tmp_path, [{"kpi": "missed", "max": 0}], default=0.9))
    assert any("the seed breaks a gate: missed = 1 > 0" in failure for failure in report.failures)
    assert report.exit_code == 2


@pytest.mark.slow
def test_a_run_directory_remembers_its_gates(tmp_path):
    Driver(_config(tmp_path, [{"kpi": "missed", "max": 0}], generations=1), run_dir=tmp_path / "run").run()
    changed = _config(tmp_path, [{"kpi": "missed", "max": 1}], generations=2)
    with pytest.raises(ValueError, match="the gates changed"):
        Driver(changed, run_dir=tmp_path / "run").run()
