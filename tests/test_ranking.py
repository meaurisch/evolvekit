"""Hold-out-aware ranking, end to end: scoring, cascade, archive, leaderboard.

Two distinct things are being tested, because the first real run showed two
distinct failure modes and one number does not catch both:

* `evaluate.holdout_penalty` charges a candidate whose private hold-out score
  is *below its own public score* -- it fitted the instances it could see.
* The leaderboard flags a candidate whose private score is below the **seed's**
  private score. That is the 2026-08-22 case exactly: the big-step winner
  improved the public full set (5.87 % -> 5.77 % excess) while its hold-out
  went 5.51 % -> 5.70 %. Both of its levels stayed better than its own public
  score, so no penalty is due; what went wrong is only visible against the
  seed, and that is where the flag is.
"""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from evolvekit.candidate import Candidate
from evolvekit.config import ConfigError, build_config, load_config
from evolvekit.evaluate.cascade import Cascade
from evolvekit.evaluate.scoring import ranking_score
from evolvekit.leaderboard import fitness_of, rank, render_markdown
from evolvekit.search.archive import Archive
from tests.conftest import EXAMPLE_CONFIG

# Scores are maximised, so the run's percentages come in negated.
PUBLIC_BEFORE, PUBLIC_AFTER = -5.87, -5.77
PRIVATE_BEFORE, PRIVATE_AFTER = -5.51, -5.70


# -- the formula -----------------------------------------------------------


def test_a_hold_out_that_did_worse_costs_exactly_the_gap():
    # public -5.0, private -5.5: the hold-out is 0.5 worse and pays 0.5.
    assert ranking_score(-5.0, -5.0, -5.5, 1.0) == pytest.approx(-5.5)


def test_a_bigger_public_gain_bought_with_a_bigger_gap_ranks_lower():
    honest = ranking_score(-5.4, -5.4, -5.4, 1.0)
    greedy = ranking_score(-5.0, -5.0, -6.0, 1.0)
    assert greedy < honest, "buying public score with the hold-out must not pay"


def test_a_hold_out_that_did_better_is_never_a_bonus():
    assert ranking_score(-5.0, -5.0, -4.0, 1.0) == -5.0
    assert ranking_score(-5.0, -5.0, -4.0, 5.0) == -5.0


def test_no_hold_out_means_no_discount():
    assert ranking_score(-5.0, -5.0, None, 1.0) == -5.0
    assert ranking_score(-5.0, None, -9.0, 1.0) == -5.0


@pytest.mark.parametrize("penalty,expected", [(0.0, -5.0), (0.5, -5.25), (2.0, -6.0)])
def test_the_penalty_scales_the_discount(penalty, expected):
    assert ranking_score(-5.0, -5.0, -5.5, penalty) == pytest.approx(expected)


def test_the_penalty_must_be_non_negative(minimal_raw, tmp_path):
    raw = copy.deepcopy(minimal_raw)
    raw["evaluate"]["holdout_penalty"] = -1.0
    with pytest.raises(ConfigError, match="only ever subtracts"):
        build_config(raw, base_dir=tmp_path)


def test_the_default_penalty_is_one(minimal_raw, tmp_path):
    assert build_config(minimal_raw, base_dir=tmp_path).evaluate.holdout_penalty == 1.0


# -- the cascade -----------------------------------------------------------


@pytest.fixture
def overfitting_project(tmp_path: Path) -> Path:
    """An evaluator that scores the public inputs well and the hold-out badly."""
    (tmp_path / "skeleton.py").write_text(
        '__all__ = ["value"]\n\n\n'
        "# EVOLVE-BLOCK-START\n"
        "def value():\n"
        '    """Seed."""\n'
        "    return 1\n"
        "# EVOLVE-BLOCK-END\n",
        encoding="utf-8",
        newline="\n",
    )
    (tmp_path / "evaluate.py").write_text(
        "import argparse, json\n"
        "from pathlib import Path\n"
        "p = argparse.ArgumentParser()\n"
        "p.add_argument('--candidate'); p.add_argument('--inputs')\n"
        "p.add_argument('--out')\n"
        "a = p.parse_args()\n"
        "score = -5.77 if a.inputs == 'public' else -5.70\n"
        "out = Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)\n"
        "out.write_text(json.dumps({'kpis': {'value': score}}), encoding='utf-8')\n",
        encoding="utf-8",
        newline="\n",
    )
    config = {
        "problem": {"skeleton": "skeleton.py", "required_functions": ["value"]},
        "evaluate": {
            "holdout_penalty": 1.0,
            "stages": [
                {"id": "static", "kind": "builtin-static"},
                {
                    "id": "full",
                    "kind": "command",
                    "command": (
                        "python evaluate.py --candidate {candidate} "
                        "--inputs {inputs} --out {out}"
                    ),
                    "inputs": ["public"],
                    "private_inputs": ["holdout"],
                    "timeout": 60,
                },
            ],
            "score": {"objective": "value", "direction": "maximize"},
        },
        "models": {
            "small": {"provider": "fake", "model": "s", "options": {"responses": ["x"]}},
            "strong": {"provider": "fake", "model": "b", "options": {"responses": ["x"]}},
        },
    }
    (tmp_path / "evolvekit.yaml").write_text(
        yaml.safe_dump(config, sort_keys=False), encoding="utf-8", newline="\n"
    )
    return tmp_path / "evolvekit.yaml"


def test_the_cascade_records_the_raw_scores_and_the_discounted_one(
    overfitting_project, tmp_path
):
    config = load_config(overfitting_project)
    candidate = Candidate(
        id="c1",
        generation=0,
        block="def value():\n    return 1\n",
        source=config.problem.skeleton.read_text(encoding="utf-8"),
        operator="human-seed",
    )
    cascade = Cascade(config, work_dir=tmp_path / "work")
    result = cascade.evaluate_generation([candidate])["c1"]

    assert result.public_score == pytest.approx(-5.77)
    assert result.private_score == pytest.approx(-5.70)
    assert result.generalization_gap == pytest.approx(-0.07)
    # The hold-out did *better* here, so nothing is discounted.
    assert result.ranking_score == pytest.approx(result.public_score)

    penalised = replace(config, evaluate=replace(config.evaluate, holdout_penalty=1.0))
    flipped = Cascade(
        replace(
            penalised,
            evaluate=replace(
                penalised.evaluate,
                score=replace(penalised.evaluate.score, direction="minimize"),
            ),
        ),
        work_dir=tmp_path / "work2",
    )
    result = flipped.evaluate_generation([candidate])["c1"]
    assert result.public_score == pytest.approx(5.77)
    assert result.private_score == pytest.approx(5.70)
    assert result.generalization_gap == pytest.approx(0.07)
    assert result.ranking_score == pytest.approx(5.70), "the gap was not charged"


# -- everything downstream ranks by it -------------------------------------


def _row(cid: str, public: float, private: float, penalty: float = 1.0) -> dict:
    score = public
    return {
        "id": cid,
        "generation": 1,
        "operator": "rewrite",
        "score": score,
        "public_score": public,
        "private_score": private,
        "generalization_gap": public - private,
        "ranking_score": ranking_score(score, public, private, penalty),
        "rejected": False,
        "parent_id": None,
        "usd": 0.0,
    }


def test_the_leaderboard_ranks_by_the_discounted_score():
    rows = [
        _row("overfitter", -5.0, -6.0),
        _row("honest", -5.4, -5.4),
    ]
    assert [r["id"] for r in rank(rows)] == ["honest", "overfitter"]
    assert fitness_of(rows[0]) < fitness_of(rows[1])
    markdown = render_markdown(rows)
    assert markdown.index("honest") < markdown.index("overfitter")


def test_a_row_from_before_the_ranking_score_falls_back_to_score():
    legacy = {"id": "old", "score": -1.0, "rejected": False}
    assert fitness_of(legacy) == -1.0
    assert [r["id"] for r in rank([legacy])] == ["old"]


def test_the_leaderboard_flags_a_private_score_below_the_seed(tmp_path):
    seed = _row("seed", -5.87, -5.51)
    seed["operator"] = "human-seed"
    rows = [seed, _row("worse-privately", -5.77, -5.70), _row("fine", -5.0, -5.0)]
    markdown = render_markdown(rows)
    assert "worse-privately !" in markdown
    assert "fine !" not in markdown
    assert "private hold-out score is below the seed's" in markdown


@pytest.mark.slow
def test_the_archive_and_the_summary_agree_on_the_winner(tmp_path):
    config = load_config(EXAMPLE_CONFIG)
    config = replace(
        config,
        search=replace(config.search, generations=2, scratchpad_every=0),
    )
    from evolvekit.search.driver import Driver

    driver = Driver(config, run_dir=tmp_path / "run")
    summary = driver.run()
    assert summary.best is not None
    assert driver.grid.best is not None
    assert summary.best.id == driver.grid.best.id
    assert summary.best.fitness == summary.best.ranking_score

    payload = json.loads(driver.ledger.archive_path.read_text(encoding="utf-8"))
    assert payload["top_k"][0]["id"] == summary.best.id
    rebuilt = Archive.from_records(driver.ledger.runs(), config.search.archive)
    assert rebuilt.best is not None and rebuilt.best.id == summary.best.id
