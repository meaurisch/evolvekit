"""`seeds: N` on a command stage: N runs, one mean, and the spread recorded.

The VRP target problem's full stage is 8 x 2.5 solver-hours and its objective
moves between runs. A single sample of a noisy evaluator cannot answer "is this
candidate better", and a nine-significant-digit fingerprint of a noisy
evaluator answers "is this candidate new" with an unconditional yes. Both are
what this file is about.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from evolvekit.candidate import Candidate
from evolvekit.config import (
    ConfigError,
    DEFAULT_SIGNATURE_DIGITS_STOCHASTIC,
    StageConfig,
    build_config,
)
from evolvekit.evaluate import Cascade, build_argv, run_command_stage


# -- config ----------------------------------------------------------------


def _stage(raw: dict, minimal_raw, tmp_path):
    minimal_raw["evaluate"]["stages"] = [
        {"id": "static", "kind": "builtin-static"},
        raw,
    ]
    return build_config(minimal_raw, base_dir=tmp_path).evaluate.stages[1]


def test_seeds_defaults_to_one_and_needs_no_placeholder(minimal_raw, tmp_path):
    stage = _stage(
        {"id": "full", "kind": "command", "command": "run {candidate} {out}"},
        minimal_raw,
        tmp_path,
    )
    assert stage.seeds == 1 and not stage.stochastic


def test_more_than_one_seed_without_the_placeholder_is_refused(minimal_raw, tmp_path):
    """Three identical runs is three times the bill for one sample."""
    with pytest.raises(ConfigError, match=r"\{seed\} placeholder"):
        _stage(
            {
                "id": "full",
                "kind": "command",
                "command": "run {candidate} {out}",
                "seeds": 3,
            },
            minimal_raw,
            tmp_path,
        )


def test_a_static_stage_cannot_be_run_several_times(minimal_raw, tmp_path):
    minimal_raw["evaluate"]["stages"] = [
        {"id": "static", "kind": "builtin-static", "seeds": 2}
    ]
    with pytest.raises(ConfigError, match="only a 'command' stage"):
        build_config(minimal_raw, base_dir=tmp_path)


def test_zero_seeds_is_refused(minimal_raw, tmp_path):
    with pytest.raises(ConfigError, match="must be >= 1"):
        _stage(
            {
                "id": "full",
                "kind": "command",
                "command": "run {candidate} {out} {seed}",
                "seeds": 0,
            },
            minimal_raw,
            tmp_path,
        )


def test_the_stochastic_digit_count_has_a_default_and_is_configurable(
    minimal_raw, tmp_path
):
    evaluate = build_config(minimal_raw, base_dir=tmp_path).evaluate
    assert evaluate.signature_digits_stochastic == DEFAULT_SIGNATURE_DIGITS_STOCHASTIC
    assert evaluate.signature_digits_stochastic < evaluate.signature_digits

    plain = StageConfig(id="a", kind="command", command="x {candidate} {out}")
    noisy = StageConfig(
        id="b", kind="command", command="x {candidate} {out} {seed}", seeds=3
    )
    assert evaluate.digits_for(plain) == evaluate.signature_digits
    assert evaluate.digits_for(noisy) == evaluate.signature_digits_stochastic


# -- argv ------------------------------------------------------------------


def test_the_seed_placeholder_is_substituted(tmp_path):
    argv = build_argv(
        "python e.py --out {out} --seed {seed}",
        candidate=tmp_path / "c.py",
        inputs=("full",),
        out=tmp_path / "o.json",
        seed=7,
    )
    assert argv[-2:] == ["--seed", "7"]


def test_a_command_without_the_placeholder_is_unaffected(tmp_path):
    argv = build_argv(
        "python e.py --out {out}",
        candidate=tmp_path / "c.py",
        inputs=(),
        out=tmp_path / "o.json",
    )
    assert "--seed" not in argv


# -- running ---------------------------------------------------------------


def _noisy_evaluator(tmp_path: Path, body: str) -> str:
    """An evaluator whose KPIs depend on `--seed`, given as a python snippet."""
    script = tmp_path / "noisy.py"
    script.write_text(
        "import json, sys, pathlib\n"
        "seed = int(sys.argv[2])\n"
        f"{body}\n"
        "pathlib.Path(sys.argv[1]).write_text(json.dumps(payload), encoding='utf-8')\n",
        encoding="utf-8",
    )
    return f'"{sys.executable}" "{script}" {{out}} {{seed}} {{candidate}}'


def _run(tmp_path: Path, body: str, seeds: int):
    stage = StageConfig(
        id="full",
        kind="command",
        command=_noisy_evaluator(tmp_path, body),
        timeout=60,
        seeds=seeds,
    )
    return run_command_stage(
        tmp_path / "candidate.py",
        stage,
        inputs=("full",),
        out_path=tmp_path / "out.json",
        cwd=tmp_path,
    )


NOISY = (
    "payload = {'kpis': {'value': 10.0 + seed, 'steady': 4.0}, "
    "'text_feedback': f'run with seed {seed}'}"
)


def test_the_kpis_are_the_mean_over_the_runs(tmp_path):
    outcome = _run(tmp_path, NOISY, seeds=3)
    assert outcome.ok and outcome.runs == 3
    assert outcome.kpis["value"] == pytest.approx(11.0)  # mean of 10, 11, 12
    assert outcome.kpis["steady"] == pytest.approx(4.0)


def test_the_spread_of_each_kpi_is_recorded(tmp_path):
    outcome = _run(tmp_path, NOISY, seeds=3)
    # stdev([10, 11, 12]) == 1.0, mean 11.0
    assert outcome.kpi_cv["value"] == pytest.approx(1.0 / 11.0)
    assert outcome.kpi_cv["steady"] == 0.0


def test_the_first_runs_feedback_is_the_one_that_is_kept(tmp_path):
    """N notes about the same program is N times the prompt for one fact."""
    outcome = _run(tmp_path, NOISY, seeds=3)
    assert outcome.text_feedback == "run with seed 0"


def test_each_run_gets_its_own_output_file(tmp_path):
    _run(tmp_path, NOISY, seeds=3)
    assert (tmp_path / "out.seed0.json").is_file()
    assert (tmp_path / "out.seed2.json").is_file()


def test_a_single_seed_writes_the_plain_output_path(tmp_path):
    outcome = _run(tmp_path, NOISY, seeds=1)
    assert outcome.runs == 1 and outcome.kpi_cv == {}
    assert (tmp_path / "out.json").is_file()


def test_vector_kpis_are_averaged_element_by_element(tmp_path):
    body = "payload = {'kpis': {'v': 1.0, 'per': [float(seed), 2.0 * seed]}}"
    outcome = _run(tmp_path, body, seeds=3)
    assert outcome.vector_kpis["per"] == pytest.approx([1.0, 2.0])


def test_ragged_vectors_fall_back_to_the_first_run(tmp_path):
    """A mean over vectors of different lengths would be a fabrication."""
    body = "payload = {'kpis': {'v': 1.0, 'per': [1.0] * (seed + 1)}}"
    outcome = _run(tmp_path, body, seeds=3)
    assert outcome.vector_kpis["per"] == [1.0]


def test_one_failing_run_fails_the_whole_stage_and_names_the_seed(tmp_path):
    body = (
        "payload = {'kpis': {'v': 1.0}}\n"
        "if seed == 1:\n"
        "    raise SystemExit(3)"
    )
    outcome = _run(tmp_path, body, seeds=3)
    assert not outcome.ok
    assert "seed 1 of 2 run" in (outcome.failure or "")
    assert outcome.runs == 2  # the third was never started


def test_the_timeout_is_per_run(tmp_path):
    """Two runs of a 3s evaluator under a 60s timeout is fine, not a timeout."""
    body = "import time\ntime.sleep(0.2)\npayload = {'kpis': {'v': float(seed)}}"
    outcome = _run(tmp_path, body, seeds=2)
    assert outcome.ok
    assert outcome.duration_s > 0.4  # the *total*, so both runs are counted


# -- the behaviour signature -----------------------------------------------


def _cascade_config(minimal_raw, tmp_path, command, seeds):
    minimal_raw["evaluate"]["stages"] = [
        {"id": "static", "kind": "builtin-static", "import_check": False},
        {"id": "full", "kind": "command", "command": command, "timeout": 60,
         "seeds": seeds},
    ]
    minimal_raw["evaluate"]["score"] = {"objective": "value"}
    return build_config(minimal_raw, base_dir=tmp_path)


def _candidate(cid: str, tail: str) -> Candidate:
    block = f"def f():\n    return 1  # {tail}\n"
    return Candidate(id=cid, generation=1, block=block, source=block, operator="diff")


def test_a_noisy_evaluator_still_catches_a_behavioural_twin(minimal_raw, tmp_path):
    """The whole point of `signature_digits_stochastic`.

    The evaluator reports a value that wobbles in the sixth digit between
    runs -- a solver that stopped a microsecond later. Two structurally
    different candidates that do the same thing must still be recognised as
    twins, and at nine digits they never would be.
    """
    script = tmp_path / "wobbly.py"
    script.write_text(
        "import json, sys, pathlib, hashlib\n"
        "seed = int(sys.argv[2])\n"
        "jitter = int(hashlib.sha256(str(seed).encode()).hexdigest()[:6], 16) % 997\n"
        "value = 7.0 + jitter * 1e-6\n"
        "pathlib.Path(sys.argv[1]).write_text(\n"
        "    json.dumps({'kpis': {'value': value}}), encoding='utf-8')\n",
        encoding="utf-8",
    )
    command = f'"{sys.executable}" "{script}" {{out}} {{seed}} {{candidate}}'
    config = _cascade_config(minimal_raw, tmp_path, command, seeds=3)

    cascade = Cascade(config, work_dir=tmp_path / "work")
    first = cascade.evaluate_generation([_candidate("a", "one way")])
    second = cascade.evaluate_generation([_candidate("b", "another way")])

    assert not first["a"].rejected
    assert second["b"].rejected
    assert second["b"].behaviour_twin_id == "a"
    assert "behavioural duplicate" in (second["b"].reject_reason or "")


def test_the_same_wobble_at_nine_digits_would_look_novel(minimal_raw, tmp_path):
    """The failure mode the coarser rounding exists to prevent, made explicit."""
    from evolvekit.evaluate.signature import behaviour_signature

    run_a = {"value": 7.000001}
    run_b = {"value": 7.000002}
    fine = dict(ignore=(), digits=9)
    coarse = dict(ignore=(), digits=DEFAULT_SIGNATURE_DIGITS_STOCHASTIC)
    assert behaviour_signature(run_a, {}, **fine) != behaviour_signature(run_b, {}, **fine)
    assert behaviour_signature(run_a, {}, **coarse) == behaviour_signature(run_b, {}, **coarse)


def test_the_spread_reaches_the_candidate_record(minimal_raw, tmp_path):
    command = _noisy_evaluator(tmp_path, NOISY)
    config = _cascade_config(minimal_raw, tmp_path, command, seeds=3)
    cascade = Cascade(config, work_dir=tmp_path / "work")
    result = cascade.evaluate_generation([_candidate("a", "x")])["a"]
    assert result.kpi_cv["value"] == pytest.approx(1.0 / 11.0)
    assert result.max_kpi_cv == pytest.approx(1.0 / 11.0)


# -- the two example evaluators accept --seed ------------------------------


def _kpis(script: Path, cwd: Path, out: Path, extra: list[str]) -> dict:
    import subprocess

    subprocess.run(
        [sys.executable, str(script), "--out", str(out), *extra],
        check=True,
        capture_output=True,
        cwd=str(cwd),
    )
    return json.loads(out.read_text(encoding="utf-8"))["kpis"]


def test_bin_packing_seed_zero_reproduces_the_published_number(example_dir, tmp_path):
    common = ["--candidate", str(example_dir / "skeleton.py"), "--inputs", "full"]
    zero = _kpis(example_dir / "evaluate.py", example_dir, tmp_path / "a.json", common)
    explicit = _kpis(
        example_dir / "evaluate.py",
        example_dir,
        tmp_path / "b.json",
        common + ["--seed", "0"],
    )
    assert zero["excess_pct"] == explicit["excess_pct"] == pytest.approx(5.871, abs=0.001)


def test_bin_packing_a_different_seed_is_a_different_instance_draw(
    example_dir, tmp_path
):
    common = ["--candidate", str(example_dir / "skeleton.py"), "--inputs", "full"]
    zero = _kpis(example_dir / "evaluate.py", example_dir, tmp_path / "a.json", common)
    one = _kpis(
        example_dir / "evaluate.py",
        example_dir,
        tmp_path / "b.json",
        common + ["--seed", "1"],
    )
    assert zero["excess_pct"] != one["excess_pct"]
    # Same distribution, so the same neighbourhood: this is resampling, not a
    # different problem.
    assert abs(zero["excess_pct"] - one["excess_pct"]) < 1.0


def test_circle_packing_accepts_a_seed_and_the_fixed_seed_ignores_it(
    example_root, tmp_path
):
    example = example_root / "circlepacking"
    common = ["--candidate", str(example / "skeleton.py"), "--inputs", "full"]
    zero = _kpis(example / "evaluate.py", example, tmp_path / "a.json", common)
    five = _kpis(
        example / "evaluate.py", example, tmp_path / "b.json", common + ["--seed", "5"]
    )
    # The seed packing draws no randomness, so it is identical under any seed.
    # A searching candidate would not be, which is exactly the distinction
    # `kpi_cv` is there to make visible.
    assert zero["sum_radii"] == five["sum_radii"]
