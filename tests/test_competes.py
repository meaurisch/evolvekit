"""Who is allowed to compete: only a candidate that finished the final stage.

A score is comparable with another score only when both came out of the same
stage on the same inputs. Four kinds of candidate used to break that and were
ranked, archived and bred from anyway:

* one whose evaluation **failed** -- it carries `evaluate.failure_score`, and
  the default `-1000` outranks every healthy candidate whose minimised cost is
  above 1000;
* one that was **not promoted** past a proxy stage (issue #6) -- its score is
  from a cheaper input set;
* one whose final stage was **skipped** by `budget.max_full_evals_per_day`;
* one whose **hold-out run failed** -- it kept its public score with no
  hold-out discount, which is the one thing the hold-out exists to prevent.

All four stay in `runs.jsonl` for the record. None of them competes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from evolvekit.budget import BudgetGuard
from evolvekit.candidate import Candidate
from evolvekit.config import build_config
from evolvekit.evaluate import Cascade
from evolvekit.leaderboard import rank, render_markdown
from evolvekit.search.archive import Archive
from evolvekit.search.driver import Driver

SKELETON = (
    "# EVOLVE-BLOCK-START\n"
    "COST = 4000\n"
    "\n"
    "\n"
    "def f():\n"
    "    return COST\n"
    "# EVOLVE-BLOCK-END\n"
)

EVALUATOR = (
    "import json, re, sys\n"
    "src = open(sys.argv[1], encoding='utf-8').read()\n"
    "inputs = sys.argv[3] if len(sys.argv) > 3 else ''\n"
    "if 'BOOM' in src:\n"
    "    sys.exit('solver crashed')\n"
    "if 'FAILS_ON_HOLDOUT' in src and inputs == 'private':\n"
    "    sys.exit('solver crashed on the hold-out')\n"
    "cost = float(re.search(r'^COST = ([0-9.]+)', src, re.M).group(1))\n"
    "if inputs == 'proxy':\n"
    "    cost = cost / 100.0\n"  # a cheaper input set: not comparable with `full`
    "open(sys.argv[2], 'w').write(json.dumps({'kpis': {'cost': cost}}))\n"
)


def _block(cost: float, *, marker: str = "") -> str:
    tag = f"# {marker}\n" if marker else ""
    return f"{tag}COST = {cost}\n\n\ndef f():\n    return COST\n"


def _reply(cost: float, *, marker: str = "") -> str:
    return f"```python\n{_block(cost, marker=marker)}```"


def _config(tmp_path: Path, *, stages: list[dict], responses: list[str], **sections):
    (tmp_path / "skeleton.py").write_text(SKELETON, encoding="utf-8")
    (tmp_path / "evaluate.py").write_text(EVALUATOR, encoding="utf-8")
    model = {"provider": "fake", "options": {"responses": responses}}
    raw = {
        "problem": {"skeleton": "skeleton.py", "required_functions": ["f"]},
        "evaluate": {
            "stages": [{"id": "static", "kind": "builtin-static"}, *stages],
            "score": {"objective": "cost", "direction": "minimize"},
            # The documented default. Every healthy cost here is above 1000.
            "failure_score": -1000.0,
        },
        "models": {"small": {**model, "model": "s"}, "strong": {**model, "model": "b"}},
        "search": {
            "children_per_generation": 2,
            "generations": 1,
            "big_step_every": 99,
            "scratchpad_every": 0,
            "operators": {"rewrite": 1.0},
            "novelty": {"near": {"method": "off"}},
        },
        **sections,
    }
    return build_config(raw, base_dir=tmp_path)


def _stage(stage_id: str, inputs: str, **extra) -> dict:
    return {
        "id": stage_id,
        "kind": "command",
        "command": f'"{sys.executable}" evaluate.py {{candidate}} {{out}} {{inputs}}',
        "inputs": [inputs],
        "timeout": 60,
        **extra,
    }


def _candidate(cid: str, block: str) -> Candidate:
    source = SKELETON.replace("COST = 4000\n\n\ndef f():\n    return COST\n", block)
    return Candidate(id=cid, generation=1, block=block, source=source, operator="rewrite")


# -- the cascade decides ---------------------------------------------------


def test_a_fully_evaluated_candidate_competes(tmp_path):
    config = _config(tmp_path, stages=[_stage("full", "full")], responses=["x"])
    cascade = Cascade(config, work_dir=tmp_path / "work")
    result = cascade.evaluate_generation([_candidate("a", _block(3900))])["a"]
    assert result.score == pytest.approx(-3900.0)
    assert result.competes is True


def test_a_failed_evaluation_does_not_compete(tmp_path):
    config = _config(tmp_path, stages=[_stage("full", "full")], responses=["x"])
    cascade = Cascade(config, work_dir=tmp_path / "work")
    result = cascade.evaluate_generation(
        [_candidate("a", _block(3900, marker="BOOM"))]
    )["a"]
    assert result.score == -1000.0  # still a finite score, still not a rejection
    assert result.rejected is False
    assert result.competes is False


def test_a_candidate_stopped_at_the_proxy_does_not_compete(tmp_path):
    """Issue #6, its minimal scenario: two children, one promotion."""
    config = _config(
        tmp_path,
        stages=[
            _stage("proxy", "proxy", promote={"top_k_per_generation": 1}),
            _stage("full", "full"),
        ],
        responses=["x"],
    )
    cascade = Cascade(config, work_dir=tmp_path / "work")
    results = cascade.evaluate_generation(
        [_candidate("promoted", _block(3900)), _candidate("stopped", _block(3950))]
    )
    assert results["promoted"].stages_reached == ["static", "proxy", "full"]
    assert results["promoted"].competes is True
    # -39.5 from the proxy set would outrank the promoted candidate's -3900.
    assert results["stopped"].stages_reached == ["static", "proxy"]
    assert results["stopped"].score == pytest.approx(-39.5)
    assert results["stopped"].competes is False


def test_a_final_stage_skipped_by_the_daily_cap_does_not_compete(tmp_path):
    config = _config(
        tmp_path,
        stages=[_stage("proxy", "proxy"), _stage("full", "full")],
        responses=["x"],
        budget={"max_full_evals_per_day": 1},
    )
    cascade = Cascade(
        config, work_dir=tmp_path / "work", budget=BudgetGuard(config.budget)
    )
    results = cascade.evaluate_generation(
        [_candidate("first", _block(3900)), _candidate("second", _block(3800))]
    )
    assert results["first"].competes is True
    assert "daily full-evaluation cap" in (results["second"].last_failure or "")
    assert results["second"].competes is False


def test_a_failed_hold_out_run_does_not_compete(tmp_path):
    config = _config(
        tmp_path,
        stages=[_stage("full", "full", private_inputs=["private"])],
        responses=["x"],
    )
    cascade = Cascade(config, work_dir=tmp_path / "work")
    results = cascade.evaluate_generation(
        [
            _candidate("sound", _block(3900)),
            _candidate("unverified", _block(3000, marker="FAILS_ON_HOLDOUT")),
        ]
    )
    assert results["sound"].private_score == pytest.approx(-3900.0)
    assert results["sound"].competes is True
    # The better public score, and no hold-out to check it against.
    assert results["unverified"].public_score == pytest.approx(-3000.0)
    assert results["unverified"].private_score is None
    assert results["unverified"].competes is False


# -- and everything downstream respects it ---------------------------------


def test_the_archive_refuses_a_candidate_that_does_not_compete(tmp_path):
    config = _config(tmp_path, stages=[_stage("full", "full")], responses=["x"])
    archive = Archive.from_config(config.search.archive)
    healthy = _candidate("healthy", _block(4000))
    healthy.score = -4000.0
    crashed = _candidate("crashed", _block(3900, marker="BOOM"))
    crashed.score = -1000.0
    crashed.competes = False
    assert archive.add(healthy).inserted is True
    assert archive.add(crashed).inserted is False
    assert archive.best is healthy
    assert [c.id for c in archive.elites()] == ["healthy"]


def test_the_leaderboard_ranks_only_candidates_that_compete():
    rows = [
        {"id": "healthy", "generation": 0, "score": -4000.0, "competes": True},
        {"id": "crashed", "generation": 1, "score": -1000.0, "competes": False},
        {"id": "legacy", "generation": 1, "score": -4100.0},  # a row from an older run
    ]
    assert [r["id"] for r in rank(rows)] == ["healthy", "legacy"]
    board = render_markdown(rows)
    assert "crashed" not in board.split("\n\n")[0]
    assert "1 did not finish the final stage" in board


@pytest.mark.slow
def test_a_crashed_child_never_becomes_best_or_a_parent(tmp_path):
    """The whole loop, with the documented default `failure_score: -1000`."""
    config = _config(
        tmp_path,
        stages=[_stage("full", "full")],
        responses=[_reply(3900, marker="BOOM"), _reply(3950)],
        search={
            "children_per_generation": 2,
            "generations": 2,
            "big_step_every": 99,
            "scratchpad_every": 0,
            "operators": {"rewrite": 1.0},
            "novelty": {"near": {"method": "off"}},
            "novelty_retry": False,
        },
    )
    driver = Driver(config, run_dir=tmp_path / "run")
    summary = driver.run()

    rows = driver.ledger.runs()
    crashed = [r for r in rows if r["score"] == -1000.0 and not r["rejected"]]
    assert crashed, "the scripted crash never happened"
    assert all(r["competes"] is False for r in crashed)

    assert summary.seed_score == pytest.approx(-4000.0)
    assert summary.best is not None
    assert summary.best.score == pytest.approx(-3950.0)
    crashed_ids = {r["id"] for r in crashed}
    assert not crashed_ids & {r["parent_id"] for r in rows}
    assert not crashed_ids & set(driver.grid.members)


def test_resuming_an_older_run_does_not_readmit_its_failed_candidates(tmp_path):
    """`runs.jsonl` rows written before `competes` existed carry no verdict, so
    resume judges them by the same rule instead of trusting the default."""
    config = _config(
        tmp_path,
        stages=[_stage("proxy", "proxy"), _stage("full", "full")],
        responses=["x"],
    )
    legacy = {"generation": 1, "operator": "rewrite", "rejected": False}
    rows = [
        {
            "id": "g000-c0001",
            "generation": 0,
            "operator": "human-seed",
            "block": _block(4000),
            "score": -4000.0,
            "stages_reached": ["static", "proxy", "full"],
        },
        {
            **legacy,
            "id": "g001-c0002",
            "block": _block(3900, marker="BOOM"),
            "score": -1000.0,
            "stages_reached": ["static"],
            "last_failure": "stage proxy: exit code 1",
        },
        {
            **legacy,
            "id": "g001-c0003",
            "block": _block(3950),
            "score": -39.5,
            "stages_reached": ["static", "proxy"],
        },
    ]
    run_dir = tmp_path / "older-run"
    run_dir.mkdir()
    (run_dir / "runs.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )

    driver = Driver(config, run_dir=run_dir)
    assert driver.resume() == 1
    assert set(driver.grid.members) == {"g000-c0001"}
    assert driver.best is not None and driver.best.id == "g000-c0001"
