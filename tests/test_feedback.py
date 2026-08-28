"""`text_feedback`: from the evaluator's JSON to the next prompt.

The KPIs are a vector of numbers, and a vector of numbers cannot say "you lost
on the Weibull instances" or "you used 0.1% of your time budget". This is the
channel that can, bounded at both ends so that a chatty evaluator cannot grow
the prompt.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from evolvekit.candidate import Candidate
from evolvekit.config import StageConfig
from evolvekit.evaluate import run_command_stage
from evolvekit.evaluate.stages import FEEDBACK_LIMIT
from evolvekit.evaluate.types import EvalResult
from evolvekit.prompts import latest_feedback, user_prompt


def _writer(tmp_path: Path, payload: str) -> str:
    """A one-line evaluator that writes `payload` verbatim to `{out}`."""
    script = tmp_path / "writer.py"
    script.write_text(
        "import sys, pathlib\n"
        f"pathlib.Path(sys.argv[1]).write_text({payload!r}, encoding='utf-8')\n",
        encoding="utf-8",
    )
    return f'"{sys.executable}" "{script}" {{out}} {{candidate}}'


def _stage(command: str) -> StageConfig:
    return StageConfig(id="proxy", kind="command", command=command, timeout=60)


def _run(tmp_path: Path, payload: str):
    return run_command_stage(
        tmp_path / "candidate.py",
        _stage(_writer(tmp_path, payload)),
        inputs=("a",),
        out_path=tmp_path / "out.json",
        cwd=tmp_path,
    )


# -- reading it off the stage output ---------------------------------------


def test_text_feedback_is_read_beside_the_kpis(tmp_path):
    outcome = _run(
        tmp_path, json.dumps({"kpis": {"value": 1.0}, "text_feedback": "  lost on wb-3  "})
    )
    assert outcome.ok
    assert outcome.kpis == {"value": 1.0}
    assert outcome.text_feedback == "lost on wb-3"


def test_an_evaluator_that_says_nothing_is_unchanged(tmp_path):
    outcome = _run(tmp_path, json.dumps({"kpis": {"value": 1.0}}))
    assert outcome.ok and outcome.text_feedback == ""


def test_the_framework_truncates_and_the_evaluator_cannot_opt_out(tmp_path):
    outcome = _run(
        tmp_path, json.dumps({"kpis": {"value": 1.0}, "text_feedback": "x" * 9000})
    )
    assert len(outcome.text_feedback) == FEEDBACK_LIMIT


def test_feedback_is_never_mistaken_for_a_kpi_in_the_flat_shape(tmp_path):
    """A document with no `kpis` key is the KPI mapping -- minus this one key."""
    outcome = _run(tmp_path, json.dumps({"value": 2.0, "text_feedback": "hello"}))
    assert outcome.ok
    assert outcome.kpis == {"value": 2.0}
    assert outcome.text_feedback == "hello"


def test_a_non_string_feedback_is_a_named_stage_failure(tmp_path):
    outcome = _run(tmp_path, json.dumps({"kpis": {"v": 1.0}, "text_feedback": [1, 2]}))
    assert not outcome.ok
    assert "text_feedback" in (outcome.failure or "")


# -- picking which one reaches the prompt ----------------------------------


def _parent(**kwargs) -> Candidate:
    return Candidate(
        id="p1", generation=1, block="def f():\n    return 1\n", source="", operator="diff", **kwargs
    )


def test_the_deepest_stage_wins():
    result = EvalResult(
        score=1.0,
        stages_reached=["static", "proxy", "full"],
        feedback={"proxy": "five instances", "full": "twenty instances"},
    )
    assert latest_feedback(result, _parent()) == "twenty instances"


def test_a_shallower_stage_is_used_when_the_deepest_said_nothing():
    result = EvalResult(
        score=1.0, stages_reached=["proxy", "full"], feedback={"proxy": "five instances"}
    )
    assert latest_feedback(result, _parent()) == "five instances"


def test_the_parents_own_record_is_used_when_there_is_no_result():
    parent = _parent(stages_reached=["proxy"], feedback={"proxy": "from the log"})
    assert latest_feedback(None, parent) == "from the log"


def test_no_feedback_anywhere_is_the_empty_string():
    assert latest_feedback(None, _parent()) == ""


# -- what the prompt does with it ------------------------------------------


def test_the_prompt_carries_an_evaluator_notes_section():
    result = EvalResult(
        score=-5.0,
        kpis={"excess_pct": 5.0},
        stages_reached=["proxy"],
        feedback={"proxy": "weibull: 5.808%\nor_like: 5.913%"},
    )
    prompt = user_prompt(_parent(), result, "diff")
    assert "## Evaluator notes" in prompt
    assert "weibull: 5.808%" in prompt
    # Beside the score breakdown, and before the operator instruction.
    assert prompt.index("## How it scored") < prompt.index("## Evaluator notes")
    assert prompt.index("## Evaluator notes") < prompt.index("## Your task")


def test_the_section_is_absent_when_the_evaluator_said_nothing():
    result = EvalResult(score=-5.0, kpis={"excess_pct": 5.0}, stages_reached=["proxy"])
    assert "## Evaluator notes" not in user_prompt(_parent(), result, "diff")


def test_the_section_is_bounded_in_lines():
    result = EvalResult(
        score=1.0,
        stages_reached=["proxy"],
        feedback={"proxy": "\n".join(f"line {i}" for i in range(200))},
    )
    prompt = user_prompt(_parent(), result, "diff")
    section = prompt.split("## Evaluator notes\n", 1)[1].split("\n\n", 1)[0]
    assert len(section.splitlines()) <= 20
    assert "line 0" in section and "line 199" not in section


# -- the two example evaluators --------------------------------------------


def test_bin_packing_reports_per_distribution_excess_and_the_worst_instance(
    example_dir, tmp_path
):
    import subprocess

    out = tmp_path / "kpis.json"
    subprocess.run(
        [
            sys.executable,
            str(example_dir / "evaluate.py"),
            "--candidate",
            str(example_dir / "skeleton.py"),
            "--inputs",
            "full",
            "--out",
            str(out),
        ],
        check=True,
        capture_output=True,
        cwd=str(example_dir),
    )
    payload = json.loads(out.read_text(encoding="utf-8"))
    note = payload["text_feedback"]
    assert "or_like:" in note and "weibull:" in note
    assert "worst single instance:" in note
    assert isinstance(payload["kpis"]["excess_pct"], float)
    assert "text_feedback" not in payload["kpis"]


def test_circle_packing_reports_the_gap_the_contacts_and_the_budget_it_used(
    example_root, tmp_path
):
    import subprocess

    example = example_root / "circlepacking"
    out = tmp_path / "kpis.json"
    subprocess.run(
        [
            sys.executable,
            str(example / "evaluate.py"),
            "--candidate",
            str(example / "skeleton.py"),
            "--inputs",
            "full",
            "--out",
            str(out),
        ],
        check=True,
        capture_output=True,
        cwd=str(example),
    )
    payload = json.loads(out.read_text(encoding="utf-8"))
    note = payload["text_feedback"]
    assert "TIME_BUDGET_S" in note
    assert "Minimum gap" in note
    assert "pressed against a wall" in note
    # The seed spends none of its budget, and the note must say so out loud --
    # that is the sentence the 2026-08-23 run never got to read.
    assert "essentially none of it" in note
    assert payload["kpis"]["min_pairwise_gap"] == pytest.approx(0.0, abs=1e-9)
    assert payload["kpis"]["boundary_contacts"] == 16.0
