"""Scoring, penalties, promotion and hard rejection.

The load-bearing assertion in this file is that no path produces a `None`
score. Everything else follows from that.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from evolvekit.candidate import Candidate
from evolvekit.config import (
    PenaltyConfig,
    PromoteRule,
    ProblemConfig,
    ScoreConfig,
    StageConfig,
    build_config,
)
from evolvekit.evaluate import (
    Cascade,
    archive_threshold,
    build_argv,
    compute_score,
    run_command_stage,
    run_static_stage,
    scale_value,
    select_promoted,
    static_checks,
)

MINIMIZE = ScoreConfig(objective="excess", direction="minimize", weights={"excess": 1.0})
MAXIMIZE = ScoreConfig(objective="gain", direction="maximize", weights={"gain": 1.0})


# -- scoring ---------------------------------------------------------------


def test_minimised_objective_becomes_a_maximised_score():
    score, penalty, terms = compute_score({"excess": 5.5}, MINIMIZE)
    assert score == pytest.approx(-5.5)
    assert penalty == 0.0 and terms == {}


def test_weights_combine_several_kpis():
    cfg = ScoreConfig(objective="a", direction="maximize", weights={"a": 2.0, "b": -1.0})
    score, _, _ = compute_score({"a": 3.0, "b": 4.0}, cfg)
    assert score == pytest.approx(2.0)


def test_a_violating_candidate_scores_lower_but_is_never_none():
    penalties = (PenaltyConfig(kpi="errors", weight=2.0, scale="log1p"),)
    clean, _, _ = compute_score({"excess": 5.0, "errors": 0.0}, MINIMIZE, penalties)
    dirty, total, terms = compute_score({"excess": 5.0, "errors": 99.0}, MINIMIZE, penalties)

    assert clean is not None and dirty is not None
    assert isinstance(dirty, float)
    assert dirty < clean
    assert total == pytest.approx(2.0 * scale_value(99.0, "log1p"))
    assert terms["errors"] > 0


def test_linear_and_log1p_scales():
    assert scale_value(9.0, "linear") == 9.0
    assert scale_value(9.0, "log1p") == pytest.approx(2.302585, abs=1e-5)
    assert scale_value(-4.0, "linear") == 4.0  # magnitude, so a penalty never rewards


def test_missing_kpi_counts_as_zero_rather_than_exploding():
    score, total, _ = compute_score(
        {}, MINIMIZE, (PenaltyConfig(kpi="absent", weight=3.0),)
    )
    assert score == 0.0 and total == 0.0


def test_non_finite_kpis_do_not_poison_the_ordering():
    score, _, _ = compute_score({"excess": float("nan")}, MINIMIZE)
    assert score == 0.0
    score, _, _ = compute_score({"gain": float("inf")}, MAXIMIZE)
    assert score == 0.0


# -- static stage ----------------------------------------------------------

PROBLEM = ProblemConfig(
    skeleton=Path("skeleton.py"), required_functions=("priority",)
)
STATIC_STAGE = StageConfig(id="static", kind="builtin-static", timeout=30)


def test_static_accepts_a_well_formed_candidate():
    problems, kpis = static_checks("def priority(item, bins):\n    return []\n", PROBLEM)
    assert problems == []
    assert kpis["complexity"] > 0


def test_static_rejects_a_syntax_error():
    problems, kpis = static_checks("def priority(:\n", PROBLEM)
    assert any("SyntaxError" in p for p in problems)
    assert kpis["complexity"] == -1.0


def test_static_rejects_a_forbidden_import():
    problems, _ = static_checks(
        "import os\n\ndef priority(item, bins):\n    return []\n", PROBLEM
    )
    assert any("forbidden import" in p and "os" in p for p in problems)


def test_static_rejects_dynamic_execution():
    problems, _ = static_checks(
        "def priority(item, bins):\n    return eval('[]')\n", PROBLEM
    )
    assert any("forbidden call" in p for p in problems)


def test_static_reports_a_missing_required_function():
    problems, _ = static_checks("def other():\n    return 1\n", PROBLEM)
    assert any("missing required top-level function" in p for p in problems)


def test_static_import_check_catches_a_module_level_crash(tmp_path):
    path = tmp_path / "boom.py"
    source = "def priority(item, bins):\n    return []\n\nraise RuntimeError('boom')\n"
    path.write_text(source, encoding="utf-8")
    outcome = run_static_stage(path, source, STATIC_STAGE, PROBLEM)
    assert outcome.ok is False
    assert "boom" in (outcome.failure or "") + outcome.stderr


def test_static_import_check_passes_a_clean_module(tmp_path):
    path = tmp_path / "fine.py"
    source = "def priority(item, bins):\n    return [1.0]\n"
    path.write_text(source, encoding="utf-8")
    outcome = run_static_stage(path, source, STATIC_STAGE, PROBLEM)
    assert outcome.ok is True and outcome.failure is None


# -- command stage ---------------------------------------------------------


def test_build_argv_splits_the_template_before_substituting(tmp_path):
    candidate = tmp_path / "a b" / "cand.py"
    out = tmp_path / "out.json"
    argv = build_argv(
        "python run.py --candidate {candidate} --inputs {inputs} --out {out}",
        candidate=candidate,
        inputs=["x", "y"],
        out=out,
    )
    # A path containing a space stays one argument; backslashes survive.
    assert argv[:2] == ["python", "run.py"]
    assert argv[argv.index("--candidate") + 1] == str(candidate)
    assert argv[argv.index("--inputs") + 1] == "x,y"
    assert argv[argv.index("--out") + 1] == str(out)


def _command_stage(script: Path, timeout: float = 30.0) -> StageConfig:
    return StageConfig(
        id="proxy",
        kind="command",
        command=f'"{sys.executable}" "{script}" {{candidate}} {{out}}',
        timeout=timeout,
    )


def test_command_stage_reads_the_kpi_json(tmp_path):
    script = tmp_path / "ok.py"
    script.write_text(
        "import json, sys\n"
        "open(sys.argv[2], 'w').write(json.dumps({'kpis': {'excess': 4.25}}))\n",
        encoding="utf-8",
    )
    outcome = run_command_stage(
        tmp_path / "cand.py",
        _command_stage(script),
        inputs=[],
        out_path=tmp_path / "out.json",
        cwd=tmp_path,
    )
    assert outcome.ok and outcome.kpis == {"excess": 4.25}


def test_command_stage_records_a_crash_without_raising(tmp_path):
    script = tmp_path / "crash.py"
    script.write_text("import sys\nsys.stderr.write('kaboom')\nraise SystemExit(3)\n", encoding="utf-8")
    outcome = run_command_stage(
        tmp_path / "cand.py",
        _command_stage(script),
        inputs=[],
        out_path=tmp_path / "out.json",
        cwd=tmp_path,
    )
    assert outcome.ok is False
    assert "exit code 3" in (outcome.failure or "")
    assert "kaboom" in outcome.stderr


@pytest.mark.slow
def test_command_stage_times_out(tmp_path):
    script = tmp_path / "slow.py"
    script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    outcome = run_command_stage(
        tmp_path / "cand.py",
        _command_stage(script, timeout=1.0),
        inputs=[],
        out_path=tmp_path / "out.json",
        cwd=tmp_path,
    )
    assert outcome.ok is False and "timeout" in (outcome.failure or "")


def test_command_stage_rejects_non_numeric_kpis(tmp_path):
    script = tmp_path / "bad.py"
    script.write_text(
        "import json, sys\n"
        "open(sys.argv[2], 'w').write(json.dumps({'kpis': {'excess': 'four'}}))\n",
        encoding="utf-8",
    )
    outcome = run_command_stage(
        tmp_path / "cand.py",
        _command_stage(script),
        inputs=[],
        out_path=tmp_path / "out.json",
        cwd=tmp_path,
    )
    assert outcome.ok is False and "must be numbers" in (outcome.failure or "")


def test_command_stage_keeps_list_valued_kpis_apart_from_the_scalars(tmp_path):
    """The evaluator contract stays backward compatible: scalars are the KPIs
    everything scores and bins by, a list is kept aside for the signature."""
    script = tmp_path / "vec.py"
    script.write_text(
        "import json, sys\n"
        "open(sys.argv[2], 'w').write(json.dumps({'kpis': "
        "{'excess': 4.25, 'per_instance': [1, 2.5, 3]}}))\n",
        encoding="utf-8",
    )
    outcome = run_command_stage(
        tmp_path / "cand.py",
        _command_stage(script),
        inputs=[],
        out_path=tmp_path / "out.json",
        cwd=tmp_path,
    )
    assert outcome.ok
    assert outcome.kpis == {"excess": 4.25}
    assert outcome.vector_kpis == {"per_instance": [1.0, 2.5, 3.0]}


def test_command_stage_rejects_a_list_of_non_numbers(tmp_path):
    script = tmp_path / "bad.py"
    script.write_text(
        "import json, sys\n"
        "open(sys.argv[2], 'w').write(json.dumps({'kpis': {'x': ['a']}}))\n",
        encoding="utf-8",
    )
    outcome = run_command_stage(
        tmp_path / "cand.py",
        _command_stage(script),
        inputs=[],
        out_path=tmp_path / "out.json",
        cwd=tmp_path,
    )
    assert outcome.ok is False
    assert "numbers or lists of numbers" in (outcome.failure or "")


def test_command_stage_rejects_output_with_no_scalar_kpi(tmp_path):
    script = tmp_path / "novec.py"
    script.write_text(
        "import json, sys\n"
        "open(sys.argv[2], 'w').write(json.dumps({'kpis': {'x': [1, 2]}}))\n",
        encoding="utf-8",
    )
    outcome = run_command_stage(
        tmp_path / "cand.py",
        _command_stage(script),
        inputs=[],
        out_path=tmp_path / "out.json",
        cwd=tmp_path,
    )
    assert outcome.ok is False and "no scalar KPI" in (outcome.failure or "")


def test_command_stage_reports_a_missing_output_file(tmp_path):
    script = tmp_path / "silent.py"
    script.write_text("pass\n", encoding="utf-8")
    outcome = run_command_stage(
        tmp_path / "cand.py",
        _command_stage(script),
        inputs=[],
        out_path=tmp_path / "out.json",
        cwd=tmp_path,
    )
    assert outcome.ok is False and "no output file" in (outcome.failure or "")


# -- promotion -------------------------------------------------------------


def test_empty_rule_promotes_everything():
    scored = [("a", 1.0), ("b", -3.0)]
    assert select_promoted(scored, PromoteRule()) == ["a", "b"]


def test_top_k_promotes_the_best_only():
    scored = [("a", 1.0), ("b", 5.0), ("c", 3.0)]
    assert select_promoted(scored, PromoteRule(top_k_per_generation=2)) == ["b", "c"]


def test_archive_percentile_promotes_above_the_bar():
    scored = [("a", 1.0), ("b", 9.0)]
    rule = PromoteRule(archive_percentile=50.0)
    assert select_promoted(scored, rule, archive_scores=[0.0, 2.0, 4.0, 6.0]) == ["b"]


def test_archive_percentile_promotes_everything_when_the_archive_is_empty():
    scored = [("a", 1.0), ("b", 9.0)]
    rule = PromoteRule(archive_percentile=90.0)
    assert select_promoted(scored, rule, archive_scores=[]) == ["a", "b"]


def test_the_two_sub_rules_are_a_union():
    scored = [("a", 10.0), ("b", 5.0), ("c", -1.0)]
    rule = PromoteRule(top_k_per_generation=1, archive_percentile=50.0)
    promoted = select_promoted(scored, rule, archive_scores=[0.0, 4.0, 8.0])
    assert promoted == ["a", "b"]


def test_archive_threshold_handles_degenerate_inputs():
    assert archive_threshold([], 50.0) is None
    assert archive_threshold([2.0], 50.0) == 2.0


# -- the cascade end to end ------------------------------------------------


def _cascade_config(tmp_path: Path, script: Path, *, penalties=None, promote=None):
    skeleton = tmp_path / "skeleton.py"
    skeleton.write_text(
        "# EVOLVE-BLOCK-START\ndef priority(item, bins):\n    return []\n"
        "# EVOLVE-BLOCK-END\n",
        encoding="utf-8",
    )
    proxy = {
        "id": "proxy",
        "kind": "command",
        "command": f'"{sys.executable}" "{script}" {{candidate}} {{out}}',
        "timeout": 60,
    }
    if promote:
        proxy["promote"] = promote
    raw = {
        "problem": {"skeleton": "skeleton.py", "required_functions": ["priority"]},
        "evaluate": {
            "stages": [{"id": "static", "kind": "builtin-static", "timeout": 30}, proxy],
            "score": {"objective": "excess", "direction": "minimize"},
            "penalties": penalties or [],
            "failure_score": -999.0,
        },
        "models": {
            "small": {"provider": "fake", "model": "s", "options": {"responses": ["x"]}},
            "strong": {"provider": "fake", "model": "b", "options": {"responses": ["x"]}},
        },
    }
    return build_config(raw, base_dir=tmp_path)


def _echo_script(tmp_path: Path) -> Path:
    """An evaluator that reads KPI values from a marker line in the candidate."""
    script = tmp_path / "echo.py"
    script.write_text(
        "import json, sys\n"
        "src = open(sys.argv[1], encoding='utf-8').read()\n"
        "line = [l for l in src.splitlines() if l.startswith('# KPIS ')][0]\n"
        "open(sys.argv[2], 'w').write(json.dumps({'kpis': json.loads(line[7:])}))\n",
        encoding="utf-8",
    )
    return script


def _candidate(cid: str, body: str) -> Candidate:
    return Candidate(id=cid, generation=1, block=body, source=body, operator="rewrite")


def test_cascade_scores_a_clean_candidate(tmp_path):
    config = _cascade_config(tmp_path, _echo_script(tmp_path))
    cascade = Cascade(config, work_dir=tmp_path / "work")
    source = '# KPIS {"excess": 4.0}\ndef priority(item, bins):\n    return []\n'
    results = cascade.evaluate_generation([_candidate("a", source)])
    result = results["a"]
    assert result.rejected is False
    assert result.score == pytest.approx(-4.0)
    assert result.stages_reached == ["static", "proxy"]


def test_cascade_penalises_rather_than_voids(tmp_path):
    config = _cascade_config(
        tmp_path,
        _echo_script(tmp_path),
        penalties=[{"kpi": "errors", "weight": 2.0, "scale": "log1p"}],
    )
    cascade = Cascade(config, work_dir=tmp_path / "work")
    clean = '# KPIS {"excess": 4.0, "errors": 0}\ndef priority(item, bins):\n    return []\n'
    dirty = '# KPIS {"excess": 4.0, "errors": 50}\ndef priority(item, bins):\n    return []\n'
    results = cascade.evaluate_generation(
        [_candidate("clean", clean), _candidate("dirty", dirty)]
    )
    assert results["dirty"].score is not None
    assert results["dirty"].score < results["clean"].score
    assert results["dirty"].rejected is False  # a violation is not a rejection
    assert results["dirty"].penalty_total > 0


def test_cascade_hard_rejects_a_stage_zero_crash(tmp_path):
    config = _cascade_config(tmp_path, _echo_script(tmp_path))
    cascade = Cascade(config, work_dir=tmp_path / "work")
    source = (
        '# KPIS {"excess": 1.0}\n'
        "def priority(item, bins):\n    return []\n\nraise RuntimeError('nope')\n"
    )
    result = cascade.evaluate_generation([_candidate("bad", source)])["bad"]
    assert result.rejected is True
    assert result.score == -999.0  # finite, not None
    assert result.stages_reached == []
    assert "nope" in (result.last_failure or "")


def test_cascade_hard_rejects_a_forbidden_import(tmp_path):
    config = _cascade_config(tmp_path, _echo_script(tmp_path))
    cascade = Cascade(config, work_dir=tmp_path / "work")
    source = '# KPIS {"excess": 1.0}\nimport os\n\ndef priority(item, bins):\n    return []\n'
    result = cascade.evaluate_generation([_candidate("bad", source)])["bad"]
    assert result.rejected is True and "forbidden import" in (result.reject_reason or "")


def test_cascade_gives_a_failed_command_the_failure_score_not_a_rejection(tmp_path):
    script = tmp_path / "always_fails.py"
    script.write_text("raise SystemExit(2)\n", encoding="utf-8")
    config = _cascade_config(tmp_path, script)
    cascade = Cascade(config, work_dir=tmp_path / "work")
    source = "def priority(item, bins):\n    return []\n"
    result = cascade.evaluate_generation([_candidate("a", source)])["a"]
    assert result.score == -999.0
    assert result.rejected is False  # only stage 0 may reject
    assert result.stages_reached == ["static"]


def test_cascade_promotes_only_the_top_k(tmp_path):
    script = _echo_script(tmp_path)
    skeleton = tmp_path / "skeleton.py"
    skeleton.write_text(
        "# EVOLVE-BLOCK-START\ndef priority(item, bins):\n    return []\n"
        "# EVOLVE-BLOCK-END\n",
        encoding="utf-8",
    )
    stage = {
        "kind": "command",
        "command": f'"{sys.executable}" "{script}" {{candidate}} {{out}}',
        "timeout": 60,
    }
    raw = {
        "problem": {"skeleton": "skeleton.py", "required_functions": ["priority"]},
        "evaluate": {
            "stages": [
                {"id": "static", "kind": "builtin-static", "timeout": 30},
                dict(stage, id="proxy", promote={"top_k_per_generation": 1}),
                dict(stage, id="full"),
            ],
            "score": {"objective": "excess", "direction": "minimize"},
            "failure_score": -999.0,
        },
        "models": {
            "small": {"provider": "fake", "model": "s", "options": {"responses": ["x"]}},
            "strong": {"provider": "fake", "model": "b", "options": {"responses": ["x"]}},
        },
    }
    config = build_config(raw, base_dir=tmp_path)
    cascade = Cascade(config, work_dir=tmp_path / "work")
    good = '# KPIS {"excess": 1.0}\ndef priority(item, bins):\n    return []\n'
    poor = '# KPIS {"excess": 9.0}\ndef priority(item, bins):\n    return []\n'
    results = cascade.evaluate_generation(
        [_candidate("good", good), _candidate("poor", poor)]
    )
    assert results["good"].stages_reached == ["static", "proxy", "full"]
    assert results["poor"].stages_reached == ["static", "proxy"]
    # Not being promoted keeps the proxy score; it is not a punishment.
    assert results["poor"].score == pytest.approx(-9.0)
