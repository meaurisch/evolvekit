"""Scoring, penalties, promotion and hard rejection.

The load-bearing assertion in this file is that no path produces a `None`
score. Everything else follows from that.
"""

from __future__ import annotations

import json
import random
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
from evolvekit.evaluate.types import StageOutcome

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


# -- promotion with a noise band -------------------------------------------
#
# The band is twice one candidate's stage-score noise (`Noise.score_sd`). Two
# candidates of equal worth differ by sd √2·score_sd, so one truly level with
# the k-th falls more than the band below it in Φ(-2/√2) ≈ 7.9 % of draws.


def test_promotion_adds_the_candidates_within_the_noise_of_the_kth():
    rule = PromoteRule(top_k_per_generation=2)
    scored = [("a", 10.0), ("b", 9.0), ("c", 8.5), ("d", 7.0), ("e", 8.8)]
    assert select_promoted(scored, rule) == ["a", "b"]
    assert select_promoted(scored, rule, band=0.0) == ["a", "b"], "band 0 is today's rule"
    assert select_promoted(scored, rule, band=1.0) == ["a", "b", "c", "e"], "8.5 and 8.8 are within 1 of 9"
    assert len(select_promoted([(f"x{i}", 1.0) for i in range(9)], rule, band=5.0)) == 4, "capped at 2k"
    ties = [(f"x{i}", 1.0) for i in range(5)]
    assert select_promoted(ties, rule) == ["x0", "x1"], "band 0 keeps today's tie-break by id, not every tie"
    assert select_promoted(ties, rule, band=5.0) == ["x0", "x1", "x2", "x3"], "the cap breaks ties by id"
    assert select_promoted(scored, rule, band=float("nan")) == ["a", "b"], "a band that is no number is 0"


def test_the_cap_does_not_hold_back_what_the_percentile_promotes():
    rule = PromoteRule(top_k_per_generation=1, archive_percentile=50)
    scored = [(f"x{i}", float(i)) for i in range(6)]
    chosen = select_promoted(scored, rule, archive_scores=[0.0, 1.0, 2.0], band=10.0)
    assert set(chosen) >= {"x5", "x4"} and {"x1", "x2", "x3"} <= set(chosen), "cap 2 on top-k + band; p50 = 1 adds x1..x3"
    assert "x0" not in chosen, "below the percentile and outside the cap"


def test_a_candidate_one_noise_sd_better_is_left_out_in_under_22_percent():
    """8 candidates, scores with sd 1 (one candidate's), k = 2, band 2, cap 4; one
    candidate truly 1 better. With the band and the cap it is left out when 4 or
    more of the 7 others measure above it (the cap), or when 2 or 3 do and it is
    more than 2 below the 2nd score (the band). The first term alone is
    E_x[P(Bin(7, Φ(-x)) >= 4)], x ~ N(1, 1) = 18.2 %; the second adds little:
    about 18 %. Today (top 2) it is left
    out when 2 of the 7 do: about 41 %. Measured through `select_promoted` (20 000
    seeded draws): see the printed rates; the bands sit outside 2 SE of both."""
    rng, rule = random.Random(31), PromoteRule(top_k_per_generation=2)
    trials, left, today = 20_000, 0, 0
    for _ in range(trials):
        scored = [("good", 1.0 + rng.gauss(0, 1))] + [(f"n{i}", rng.gauss(0, 1)) for i in range(7)]
        left += "good" not in select_promoted(scored, rule, band=2.0)
        today += "good" not in select_promoted(scored, rule)
    p_band, p_today = left / trials, today / trials
    se_band, se_today = (p_band * (1 - p_band) / trials) ** 0.5, (p_today * (1 - p_today) / trials) ** 0.5
    print(f"left out with the band: {p_band:.2%} (SE {se_band:.2%}); today: {p_today:.2%} (SE {se_today:.2%})")
    assert p_band < 0.22 and p_today > 0.38


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


def test_a_stage_without_a_hold_out_is_never_vetoed(tmp_path):
    config = _cascade_config(tmp_path, _echo_script(tmp_path))
    cascade = Cascade(config, work_dir=tmp_path / "work")
    cascade.seed_private = 100.0  # would veto anything, were there a hold-out
    result = cascade.evaluate_generation(
        [_candidate("a", '# KPIS {"excess": 4.0}\ndef priority(item, bins):\n    return []\n')]
    )["a"]
    # `proxy` is the final stage here and has no hold-out: it competes, unvetoed.
    assert result.competes is True and result.veto is None


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


def _proxy_then_full(tmp_path: Path, promote: dict):
    """static -> proxy (`promote`, one run) -> full, scored on `gain` (maximised)."""
    script = _echo_script(tmp_path)
    (tmp_path / "skeleton.py").write_text(
        "# EVOLVE-BLOCK-START\ndef priority(item, bins):\n    return []\n# EVOLVE-BLOCK-END\n",
        encoding="utf-8",
    )
    stage = {"kind": "command", "command": f'"{sys.executable}" "{script}" {{candidate}} {{out}}', "timeout": 60}
    raw = {
        "problem": {"skeleton": "skeleton.py", "required_functions": ["priority"]},
        "evaluate": {
            "stages": [
                {"id": "static", "kind": "builtin-static", "timeout": 30},
                dict(stage, id="proxy", promote=promote),
                dict(stage, id="full"),
            ],
            "score": {"objective": "gain", "direction": "maximize"},
            "failure_score": -999.0,
        },
        "models": {
            "small": {"provider": "fake", "model": "s", "options": {"responses": ["x"]}},
            "strong": {"provider": "fake", "model": "b", "options": {"responses": ["x"]}},
        },
    }
    return build_config(raw, base_dir=tmp_path)


def _gain(cid: str, gain: float, **kpis: float) -> Candidate:
    reported = json.dumps({"gain": gain, **kpis})
    return _candidate(cid, f"# KPIS {reported}\ndef priority(item, bins):\n    return []\n")


def _replicated(stage_id: str, var: float) -> StageOutcome:
    """A finished three-seed outcome whose `gain` varies by `var` between seeds."""
    return StageOutcome(stage_id=stage_id, ok=True, kpis={"gain": 0.0}, kpi_var={"gain": var},
                        runs=3, seed_list=(0, 1, 2))


def test_with_no_noise_promotion_is_top_k(tmp_path):
    config = _proxy_then_full(tmp_path, {"top_k_per_generation": 1})
    cascade = Cascade(config, work_dir=tmp_path / "work")
    assert not cascade.noise.known("proxy")
    results = cascade.evaluate_generation([_gain("a", 10.0), _gain("b", 9.9)])
    assert results["a"].stages_reached == ["static", "proxy", "full"]
    assert results["b"].stages_reached == ["static", "proxy"], "no noise: no band, top 1 as before"


def test_a_screen_of_one_run_has_no_band_even_when_the_final_stage_is_noisy(tmp_path):
    """In a study the screen runs `seeds: 1` and is never calibrated (calibration is
    the final stage's): its own noise is 0, so its band is 0 -- today's top k."""
    config = _proxy_then_full(tmp_path, {"top_k_per_generation": 1})
    cascade = Cascade(config, work_dir=tmp_path / "work")
    cascade.noise.observe("full", "z", _replicated("full", 100.0))
    assert cascade.noise.sd("full", "gain") == pytest.approx(10.0) and cascade.noise.sd("proxy", "gain") == 0.0
    results = cascade.evaluate_generation([_gain("a", 10.0), _gain("b", 9.0)])
    assert results["b"].stages_reached == ["static", "proxy"]


@pytest.mark.parametrize("order", [("a", "b", "c"), ("c", "b", "a")])
def test_a_candidate_within_the_stages_noise_of_the_kth_is_promoted(tmp_path, order):
    """One measure, no other to tell them apart: the twin margin (2·sd at one
    run) equals the band, yet b is not a's noise twin -- they are of the same
    round (Decision 15) -- whichever of them is evaluated first."""
    config = _proxy_then_full(tmp_path, {"top_k_per_generation": 1})
    cascade = Cascade(config, work_dir=tmp_path / "work")
    cascade.noise.observe("proxy", "z", _replicated("proxy", 4.0))  # one run's sd 2: band 4
    gains = {"a": 10.0, "b": 7.0, "c": 5.0}
    results = cascade.evaluate_generation([_gain(cid, gains[cid]) for cid in order])
    assert all(r.behaviour_twin_id is None for r in results.values())
    assert results["a"].stages_reached == ["static", "proxy", "full"]
    assert results["b"].stages_reached == ["static", "proxy", "full"], "7 is within 4 of 10"
    assert results["c"].stages_reached == ["static", "proxy"], "5 is not"


@pytest.mark.parametrize("order", [("a", "b"), ("b", "a")])
def test_a_near_equal_pair_of_one_round_is_not_twinned(tmp_path, order):
    config = _proxy_then_full(tmp_path, {"top_k_per_generation": 2})
    cascade = Cascade(config, work_dir=tmp_path / "work")
    cascade.noise.observe("proxy", "z", _replicated("proxy", 4.0))
    gains = {"a": 10.0, "b": 9.99}
    results = cascade.evaluate_generation([_gain(cid, gains[cid]) for cid in order])
    assert results["a"].behaviour_twin_id is None and results["b"].behaviour_twin_id is None
    assert results["b"].stages_reached == ["static", "proxy", "full"]


@pytest.mark.parametrize("order", [("a", "b"), ("b", "a")])
def test_an_identical_pair_of_one_round_is_twinned_by_the_exact_hash(tmp_path, order):
    config = _proxy_then_full(tmp_path, {"top_k_per_generation": 2})
    cascade = Cascade(config, work_dir=tmp_path / "work")
    cascade.noise.observe("proxy", "z", _replicated("proxy", 4.0))
    results = cascade.evaluate_generation([_gain(cid, 10.0) for cid in order])
    first, second = order
    assert results[first].behaviour_twin_id is None
    assert results[second].behaviour_twin_id == first and results[second].stages_reached == ["static", "proxy"]


def test_a_child_near_an_earlier_rounds_candidate_is_still_its_twin(tmp_path):
    config = _proxy_then_full(tmp_path, {"top_k_per_generation": 2})
    cascade = Cascade(config, work_dir=tmp_path / "work")
    cascade.noise.observe("proxy", "z", _replicated("proxy", 4.0))
    cascade.evaluate_generation([_gain("a", 10.0)])  # round 1
    later = _gain("b", 9.99)
    later.generation = 2
    result = cascade.evaluate_generation([later])["b"]
    assert result.behaviour_twin_id == "a" and result.stages_reached == ["static", "proxy"]


def test_the_archive_percentile_compares_the_stages_own_scores(tmp_path):
    """A proxy score of 50 against members whose fitness is around 1000 and whose own
    proxy scores are around 40: promoted at p50 (it beats their proxy scores), where
    the old comparison with the fitness refused it."""
    config = _proxy_then_full(tmp_path, {"archive_percentile": 50})
    cascade = Cascade(config, work_dir=tmp_path / "work")
    results = cascade.evaluate_generation(
        [_gain("c", 50.0)], [1000.0, 1001.0], stage_scores={"proxy": [30.0, 40.0, 45.0]}
    )
    assert results["c"].stages_reached == ["static", "proxy", "full"]
    legacy = Cascade(config, work_dir=tmp_path / "legacy")
    for scores in (None, {}, {"proxy": []}):
        refused = legacy.evaluate_generation([_gain("c", 50.0)], [1000.0, 1001.0], stage_scores=scores)
        assert refused["c"].stages_reached == ["static", "proxy"], "no own scores: the fitness, as before"


def _member(cid: str, *, competes: bool, stage_scores: dict) -> Candidate:
    member = _gain(cid, 0.0)
    member.score, member.competes, member.stage_scores = 1000.0, competes, stage_scores
    return member


def test_the_driver_hands_the_cascade_each_stages_own_archive_scores(tmp_path):
    from evolvekit.search.driver import Driver

    driver = Driver(_proxy_then_full(tmp_path, {"archive_percentile": 50}), run_dir=tmp_path / "run")
    driver.archive = [
        _member("m1", competes=True, stage_scores={"proxy": 40.0, "full": 1000.0}),
        _member("m2", competes=True, stage_scores={"full": 1001.0}),  # a row from before stage scores were kept
        _member("m3", competes=False, stage_scores={"proxy": 99.0, "full": 99.0}),
    ]
    assert driver._archive_stage_scores() == {"proxy": [40.0], "full": [1000.0, 1001.0]}

    seen: list[dict] = []

    class Seen(Exception):
        pass

    def capture(candidates, archive_scores=(), **kwargs):
        seen.append({"archive_scores": list(archive_scores), **kwargs})
        raise Seen

    driver.cascade.evaluate_generation = capture
    with pytest.raises(Seen):
        driver._evaluate_and_record([_gain("c", 50.0)], generation=1)
    assert seen == [{"archive_scores": [1000.0, 1000.0], "stage_scores": {"proxy": [40.0], "full": [1000.0, 1001.0]}}]

    # A tree problem with a build evaluates one candidate at a time: each call
    # gets the same stage scores.
    from dataclasses import replace
    from types import SimpleNamespace

    driver.config = replace(driver.config, problem=replace(driver.config.problem, tree=SimpleNamespace(build="make")))
    seen.clear()
    with pytest.raises(Seen):
        driver._evaluate_and_record([_gain("c", 50.0)], generation=1)
    assert seen == [{"archive_scores": [1000.0, 1000.0], "stage_scores": {"proxy": [40.0], "full": [1000.0, 1001.0]}}]


# -- an objective the evaluator never reported, or reported as NaN ----------
#
# Under `direction: minimize` a healthy cost of 40 scores -40. A missing or
# non-finite objective used to be coerced to 0.0, which scores -0.0 -- the best
# score in the run. One crashed solver printing `nan` was enough to make a
# broken candidate the run's best and every later generation's parent.


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity", "1" + "0" * 400])
def test_command_stage_rejects_a_non_finite_kpi(tmp_path, token):
    script = tmp_path / "nonfinite.py"
    script.write_text(
        "import sys\n"
        f"open(sys.argv[2], 'w').write('{{\"kpis\": {{\"excess\": {token}}}}}')\n",
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
    assert "'excess'" in (outcome.failure or "")
    assert "finite" in (outcome.failure or "")


def test_command_stage_rejects_a_non_finite_entry_in_a_list_kpi(tmp_path):
    script = tmp_path / "nonfinite_list.py"
    script.write_text(
        "import sys\n"
        "open(sys.argv[2], 'w').write("
        "'{\"kpis\": {\"excess\": 4.0, \"per_instance\": [1.0, NaN]}}')\n",
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
    assert "'per_instance'" in (outcome.failure or "")
    assert "finite" in (outcome.failure or "")


def test_command_stage_fails_when_a_required_kpi_is_not_reported(tmp_path):
    script = tmp_path / "other_kpis.py"
    script.write_text(
        "import json, sys\n"
        "open(sys.argv[2], 'w').write(json.dumps({'kpis': {'loss': 10, 'a': 0}}))\n",
        encoding="utf-8",
    )
    outcome = run_command_stage(
        tmp_path / "cand.py",
        _command_stage(script),
        inputs=[],
        out_path=tmp_path / "out.json",
        cwd=tmp_path,
        required_kpis=("excess",),
    )
    assert outcome.ok is False
    # Actionable: names the KPI that was wanted and the ones that arrived.
    assert "'excess'" in (outcome.failure or "")
    assert "a, loss" in (outcome.failure or "")


def test_cascade_scores_a_missing_objective_as_a_failure(tmp_path):
    config = _cascade_config(tmp_path, _echo_script(tmp_path))
    cascade = Cascade(config, work_dir=tmp_path / "work")
    healthy = '# KPIS {"excess": 40.0}\ndef priority(item, bins):\n    return []\n'
    broken = '# KPIS {"loss": 1.0}\ndef priority(item, bins):\n    return [0]\n'
    results = cascade.evaluate_generation(
        [_candidate("healthy", healthy), _candidate("broken", broken)]
    )
    assert results["healthy"].score == pytest.approx(-40.0)
    assert results["broken"].score == -999.0  # evaluate.failure_score
    assert results["broken"].score < results["healthy"].score
    assert results["broken"].stages_reached == ["static"]
    assert "'excess'" in (results["broken"].last_failure or "")


def test_cascade_scores_a_nan_objective_as_a_failure(tmp_path):
    script = tmp_path / "nan_for_broken.py"
    script.write_text(
        "import sys\n"
        "src = open(sys.argv[1], encoding='utf-8').read()\n"
        "value = 'NaN' if 'BROKEN' in src else '40.0'\n"
        "open(sys.argv[2], 'w').write('{\"kpis\": {\"excess\": ' + value + '}}')\n",
        encoding="utf-8",
    )
    config = _cascade_config(tmp_path, script)
    cascade = Cascade(config, work_dir=tmp_path / "work")
    healthy = "def priority(item, bins):\n    return []\n"
    broken = "# BROKEN\ndef priority(item, bins):\n    return [0]\n"
    results = cascade.evaluate_generation(
        [_candidate("healthy", healthy), _candidate("broken", broken)]
    )
    assert results["healthy"].score == pytest.approx(-40.0)
    assert results["broken"].score == -999.0
    assert results["broken"].score < results["healthy"].score
    assert results["broken"].stages_reached == ["static"]
    assert "finite" in (results["broken"].last_failure or "")
