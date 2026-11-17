"""Hold-out-aware ranking, end to end: scoring, cascade, archive, leaderboard.

Two distinct things are being tested, because the first real run showed two
distinct failure modes and one number does not catch both:

* `evaluate.holdout_penalty`, when set above its default of 0, charges a
  candidate whose private hold-out score is *below its own public score* -- it
  fitted the instances it could see.
* `evaluate.holdout_veto` (on by default) keeps a candidate whose private score
  is below the **seed's** private score from competing, and the leaderboard
  flags the same condition. That is the 2026-08-22 case exactly: the big-step
  winner improved the public full set (5.87 % -> 5.77 % excess) while its
  hold-out went 5.51 % -> 5.70 %. Both of its levels stayed better than its own
  public score, so no penalty is due; what went wrong is only visible against
  the seed, and that is where the veto is.
"""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from evolvekit.candidate import Candidate
from evolvekit.config import ConfigError, LevelConfig, build_config, load_config
from evolvekit.evaluate.cascade import Cascade, finished_final_stage
from evolvekit.evaluate.scoring import ranking_score
from evolvekit.evaluate.types import StageOutcome
from evolvekit.stats import t_scale
from evolvekit.leaderboard import (
    competes,
    fitness_of,
    rank,
    render_html,
    render_markdown,
    replay_veto,
    seed_private,
    unfinished_count,
    vetoed_count,
)
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


def test_the_default_penalty_is_zero_and_the_veto_is_on(minimal_raw, tmp_path):
    evaluate = build_config(minimal_raw, base_dir=tmp_path).evaluate
    assert evaluate.holdout_penalty == 0.0
    assert evaluate.holdout_veto is True


def test_the_veto_switch_must_be_a_flag(minimal_raw, tmp_path):
    raw = copy.deepcopy(minimal_raw)
    raw["evaluate"]["holdout_veto"] = "sometimes"
    with pytest.raises(ConfigError, match="holdout_veto"):
        build_config(raw, base_dir=tmp_path)


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


def test_with_no_penalty_the_archive_orders_by_the_public_score():
    rows = [_row("overfitter", -5.0, -6.0, penalty=0.0), _row("honest", -5.4, -5.4, penalty=0.0)]
    assert [r["id"] for r in rank(rows)] == ["overfitter", "honest"]
    assert all(fitness_of(r) == r["public_score"] for r in rows)


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
    # Written before the veto: it is kept out, and named under the table with the flag.
    assert [r["id"] for r in rank(rows)] == ["fine", "seed"]
    assert "1 kept out by the hold-out" in markdown


# -- the veto --------------------------------------------------------------

VALUE_SKELETON = (
    '__all__ = ["value"]\n\n\n'
    "# EVOLVE-BLOCK-START\n"
    "def value():\n"
    "    return {v}\n"
    "# EVOLVE-BLOCK-END\n"
)


@pytest.fixture
def mirror_project(tmp_path: Path) -> Path:
    """Public score `value()`, hold-out score `-value()`: every public gain is a hold-out loss."""
    (tmp_path / "skeleton.py").write_text(VALUE_SKELETON.format(v=1), encoding="utf-8", newline="\n")
    (tmp_path / "evaluate.py").write_text(
        "import argparse, json, runpy\n"
        "from pathlib import Path\n"
        "p = argparse.ArgumentParser()\n"
        "p.add_argument('--candidate'); p.add_argument('--inputs'); p.add_argument('--out')\n"
        "a = p.parse_args()\n"
        "v = float(runpy.run_path(a.candidate)['value']())\n"
        "score = v if a.inputs == 'public' else -v\n"
        "out = Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)\n"
        "out.write_text(json.dumps({'kpis': {'value': score}}), encoding='utf-8')\n",
        encoding="utf-8",
        newline="\n",
    )
    config = {
        "problem": {"skeleton": "skeleton.py", "required_functions": ["value"]},
        "evaluate": {
            "stages": [
                {"id": "static", "kind": "builtin-static"},
                {"id": "full", "kind": "command", "timeout": 60,
                 "command": "python evaluate.py --candidate {candidate} --inputs {inputs} --out {out}",
                 "inputs": ["public"], "private_inputs": ["holdout"]},
            ],
            "score": {"objective": "value", "direction": "maximize"},
        },
        "models": {
            "small": {"provider": "fake", "model": "s", "options": {"responses": ["x"]}},
            "strong": {"provider": "fake", "model": "b", "options": {"responses": ["x"]}},
        },
    }
    (tmp_path / "evolvekit.yaml").write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8", newline="\n")
    return tmp_path / "evolvekit.yaml"


def _value_candidate(cid: str, v: float, operator: str = "rewrite") -> Candidate:
    return Candidate(id=cid, generation=0 if operator == "human-seed" else 1,
                     block=f"def value():\n    return {v}\n",
                     source=VALUE_SKELETON.format(v=v), operator=operator)


def test_a_candidate_below_the_seeds_hold_out_does_not_compete(mirror_project, tmp_path):
    config = load_config(mirror_project)
    cascade = Cascade(config, work_dir=tmp_path / "work")
    seed = cascade.evaluate_generation([_value_candidate("g000-c0001", 1, "human-seed")])["g000-c0001"]
    assert seed.competes and cascade.seed_private == pytest.approx(-1.0)

    results = cascade.evaluate_generation([_value_candidate("hi", 2), _value_candidate("lo", 0.5)])
    assert results["hi"].public_score == pytest.approx(2.0), "the best public score..."
    assert results["hi"].competes is False, "...is vetoed by its hold-out"
    assert results["hi"].veto == "holdout below the seed's -1"
    assert results["hi"].ranking_score == pytest.approx(2.0), "lambda = 0: the score itself is untouched"
    assert results["lo"].competes is True and results["lo"].veto is None


def test_with_no_noise_the_veto_is_the_literal_rule(mirror_project, tmp_path):
    """No `seeds: N`, no `{seed}`: sigma-hat is 0, the margin is 0, and A0's rule
    (the test above) holds unchanged; every finished result says so."""
    config = load_config(mirror_project)
    cascade = Cascade(config, work_dir=tmp_path / "work")
    seed = cascade.evaluate_generation([_value_candidate("g000-c0001", 1, "human-seed")])["g000-c0001"]
    assert seed.holdout_margin == 0.0 and cascade.holdout_margin() == 0.0
    results = cascade.evaluate_generation([_value_candidate("hi", 1.001), _value_candidate("lo", 0.5)])
    assert results["hi"].competes is False and results["hi"].veto == "holdout below the seed's -1"
    assert results["hi"].holdout_margin == 0.0 and results["lo"].holdout_margin == 0.0


T1 = t_scale(2**0.5, 1)
"""The margin's widening to Student's t when its sd rests on two runs (1 df):
2.80 (Decision 17b). The fixtures below observe `runs=2`."""


def test_a_hold_out_below_the_seed_within_the_noise_margin_competes(mirror_project, tmp_path):
    config = load_config(mirror_project)
    cascade = Cascade(config, work_dir=tmp_path / "work")
    cascade.evaluate_generation([_value_candidate("g000-c0001", 1, "human-seed")])
    cascade.noise.observe("full/private", "x", StageOutcome(stage_id="full", ok=True, kpis={"value": -1.0},
                                                            kpi_var={"value": 0.25}, runs=2))
    # margin = 2 * 0.5 / sqrt(1) * T1 = 2.8 (seeds: 1): -3.5 is above -1 - 2.8
    result = cascade.evaluate_generation([_value_candidate("hi", 3.5)])["hi"]
    assert result.competes is True and result.holdout_margin == pytest.approx(1.0 * T1)
    worse = cascade.evaluate_generation([_value_candidate("far", 4.0)])["far"]
    assert worse.competes is False
    assert worse.veto == "holdout below the seed's -1 by more than its noise margin 2.8"


def test_the_public_noise_is_not_rescaled_across_a_change_of_sign(mirror_project, tmp_path):
    """mirror_project: the seed's public score is 1, its private -1. With only the public
    stage's noise known, |-1/1| would rescale it; the signs differ, so the margin is 0."""
    config = load_config(mirror_project)
    cascade = Cascade(config, work_dir=tmp_path / "work")
    cascade.evaluate_generation([_value_candidate("g000-c0001", 1, "human-seed")])
    cascade.noise.observe("full", "x", StageOutcome(stage_id="full", ok=True, kpis={"value": 1.0},
                                                    kpi_var={"value": 0.25}, runs=2))
    assert cascade.holdout_margin() == 0.0


def test_without_a_hold_out_estimate_the_public_noise_is_rescaled_to_the_hold_outs_size(mirror_project, tmp_path):
    """A hold-out at `seeds: 1` replicates nothing; the public run's noise, scaled by
    |seed_private / seed_public| = 0.5 when both have one sign, stands in for it."""
    config = load_config(mirror_project)
    cascade = Cascade(config, work_dir=tmp_path / "work")
    cascade.evaluate_generation([_value_candidate("g000-c0001", 1, "human-seed")])
    cascade.noise.observe("full", "x", StageOutcome(stage_id="full", ok=True, kpis={"value": 1.0},
                                                    kpi_var={"value": 0.25}, runs=2))
    cascade.seed_public, cascade.seed_private = 2.0, 1.0
    assert cascade.holdout_margin() == pytest.approx(2 * 0.5 * 0.5 * T1)
    cascade.seed_public = 0.0
    assert cascade.holdout_margin() == 0.0, "a public score of 0: no rescale"
    cascade.noise.observe("full/private", "x", StageOutcome(stage_id="full", ok=True, kpis={"value": 1.0},
                                                            kpi_var={"value": 0.04}, runs=2))
    assert cascade.holdout_margin() == pytest.approx(0.4 * T1), "the hold-out's own estimate comes first"


def test_a_repeatable_hold_out_uses_its_own_noise_only_when_it_is_paired(mirror_project, tmp_path):
    """On a repeatable hold-out the accessors read the paired pool; an across-only
    estimate there says nothing they would read, so the public noise stands in."""
    config = load_config(mirror_project)
    cascade = Cascade(config, work_dir=tmp_path / "work")
    cascade.evaluate_generation([_value_candidate("g000-c0001", 1, "human-seed")])
    cascade.seed_public, cascade.seed_private = 2.0, 1.0
    cascade.noise.observe("full", "x", StageOutcome(stage_id="full", ok=True, kpis={"value": 1.0},
                                                    kpi_var={"value": 0.25}, runs=2))
    cascade.noise.set_repeatable("full/private", True)
    cascade.noise.observe("full/private", "x", StageOutcome(stage_id="full", ok=True, kpis={"value": 1.0},
                                                            kpi_var={"value": 0.04}, runs=2))
    assert cascade.holdout_margin() == pytest.approx(2 * 0.5 * 0.5 * T1), "across only: the rescaled public noise"

    def paired(values):
        return StageOutcome(stage_id="full", ok=True, kpis={"value": sum(values) / 2},
                            kpi_var={"value": 0.0}, runs=2, seed_runs={"value": values}, seed_list=(0, 1))

    cascade.noise.observe("full/private", "s", paired([1.0, 3.0]), is_seed=True)
    cascade.noise.observe("full/private", "y", paired([1.2, 2.8]))
    # Two candidates on two seeds: the two-way residual is var_d / 2 with 1 df.
    # d = (0.2, -0.2): var_d = 0.08, one side 0.04, sd 0.2, margin 0.4 * T1
    assert cascade.holdout_margin() == pytest.approx(0.4 * T1), "paired data: the hold-out's own estimate"


def test_with_a_score_by_levels_the_margin_is_zero(mirror_project, tmp_path):
    config = load_config(mirror_project)
    cascade = Cascade(config, work_dir=tmp_path / "work")
    cascade.noise.observe("full/private", "x", StageOutcome(stage_id="full", ok=True, kpis={"value": -1.0},
                                                            kpi_var={"value": 0.25}, runs=2))
    assert cascade.holdout_margin() == pytest.approx(1.0 * T1)
    levels = (LevelConfig(kpi="value", direction="maximize", tolerance=0.1),
              LevelConfig(kpi="value", direction="maximize"))
    score = replace(config.evaluate.score, levels=levels)
    cascade.config = replace(config, evaluate=replace(config.evaluate, score=score))
    assert cascade.holdout_margin() == 0.0


def test_the_veto_can_be_switched_off(mirror_project, tmp_path):
    config = load_config(mirror_project)
    config = replace(config, evaluate=replace(config.evaluate, holdout_veto=False))
    cascade = Cascade(config, work_dir=tmp_path / "work")
    cascade.evaluate_generation([_value_candidate("g000-c0001", 1, "human-seed")])
    hi = cascade.evaluate_generation([_value_candidate("hi", 2)])["hi"]
    assert hi.competes is True and hi.veto is None


def test_no_seed_hold_out_means_no_veto(mirror_project, tmp_path):
    config = load_config(mirror_project)
    assert finished_final_stage(config, rejected=False, last_failure=None, stages_reached=["static", "full"],
                                private_score=-5.0, seed_private=None) is True
    assert finished_final_stage(config, rejected=False, last_failure=None, stages_reached=["static", "full"],
                                private_score=-5.0, seed_private=-1.0) is False


def test_a_resumed_run_rebuilds_the_seeds_hold_out_from_runs_jsonl(mirror_project, tmp_path):
    from evolvekit.search.driver import Driver

    config = load_config(mirror_project)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    rows = [
        {"id": "g000-c0001", "generation": 0, "operator": "human-seed", "block": "def value():\n    return 1\n",
         "score": 1.0, "public_score": 1.0, "private_score": -1.0, "rejected": False, "competes": True,
         "stages_reached": ["static", "full"]},
        # Written before `competes` existed: settled by today's rule, veto included.
        {"id": "g001-c0002", "generation": 1, "operator": "rewrite", "parent_id": "g000-c0001",
         "block": "def value():\n    return 2\n", "score": 2.0, "public_score": 2.0, "private_score": -2.0,
         "rejected": False, "stages_reached": ["static", "full"]},
        # Written before the veto existed, `competes: true`: the veto applies on replay as it would live.
        {"id": "g001-c0003", "generation": 1, "operator": "rewrite", "parent_id": "g000-c0001",
         "block": "def value():\n    return 3\n", "score": 3.0, "public_score": 3.0, "private_score": -3.0,
         "rejected": False, "competes": True, "stages_reached": ["static", "full"]},
    ]
    (run_dir / "runs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    driver = Driver(config, run_dir=run_dir)
    driver.resume()
    assert driver.cascade.seed_private == pytest.approx(-1.0)
    assert driver.by_id["g001-c0002"].competes is False
    assert driver.by_id["g001-c0003"].competes is False
    assert driver.by_id["g001-c0003"].veto == "holdout below the seed's -1"
    assert driver.by_id["g000-c0001"].competes is True
    assert [c.id for c in driver.top_k(3)] == ["g000-c0001"]


def test_a_row_without_a_margin_is_judged_by_the_literal_rule(mirror_project, tmp_path):
    """Rows written before A2 carry no `holdout_margin`: the resumed run judges
    them as A0 did. A row judged live with a margin keeps that margin."""
    from evolvekit.search.driver import Driver

    config = load_config(mirror_project)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    seed = {"id": "g000-c0001", "generation": 0, "operator": "human-seed", "block": "def value():\n    return 1\n",
            "score": 1.0, "public_score": 1.0, "private_score": -1.0, "rejected": False, "competes": True,
            "stages_reached": ["static", "full"]}
    rows = [
        seed,
        # Before A2, before `competes`: settled and vetoed by the literal rule.
        {"id": "g001-c0002", "generation": 1, "operator": "rewrite", "parent_id": "g000-c0001",
         "block": "def value():\n    return 1.5\n", "score": 1.5, "public_score": 1.5, "private_score": -1.5,
         "rejected": False, "stages_reached": ["static", "full"]},
        # Before A2, `competes: true`, no `veto`: the replay applies the literal rule.
        {"id": "g001-c0003", "generation": 1, "operator": "rewrite", "parent_id": "g000-c0001",
         "block": "def value():\n    return 1.2\n", "score": 1.2, "public_score": 1.2, "private_score": -1.2,
         "rejected": False, "competes": True, "stages_reached": ["static", "full"]},
        # Judged live within its margin of 1: it competes on resume too.
        {"id": "g001-c0004", "generation": 1, "operator": "rewrite", "parent_id": "g000-c0001",
         "block": "def value():\n    return 1.4\n", "score": 1.4, "public_score": 1.4, "private_score": -1.4,
         "rejected": False, "competes": True, "veto": None, "holdout_margin": 1.0,
         "stages_reached": ["static", "full"]},
    ]
    (run_dir / "runs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    driver = Driver(config, run_dir=run_dir)
    driver.resume()
    assert driver.cascade.seed_public == pytest.approx(1.0) and driver.cascade.seed_private == pytest.approx(-1.0)
    assert driver.by_id["g001-c0002"].competes is False
    assert driver.by_id["g001-c0002"].veto == "holdout below the seed's -1"
    assert driver.by_id["g001-c0003"].competes is False
    assert driver.by_id["g001-c0003"].veto == "holdout below the seed's -1"
    assert driver.by_id["g001-c0002"].holdout_margin is None
    assert driver.by_id["g001-c0004"].competes is True and driver.by_id["g001-c0004"].holdout_margin == 1.0
    assert [c.id for c in driver.top_k(3)] == ["g001-c0004", "g000-c0001"]
    assert [r["id"] for r in rank(rows)] == ["g001-c0004", "g000-c0001"], "the views agree"


def test_a_resumed_run_ranks_old_rows_with_the_current_penalty(mirror_project, tmp_path):
    """Rows written at lambda = 1 store a discounted ranking score. On resume it is
    recomputed with the run's lambda (0 by default), so the order is the public one."""
    from evolvekit.search.driver import Driver

    config = load_config(mirror_project)
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    def old(cid: str, generation: int, operator: str, public: float, private: float) -> dict:
        return {"id": cid, "generation": generation, "operator": operator,
                "parent_id": None if generation == 0 else "g000-c0001",
                "block": f"def value():\n    return {public}\n", "score": public, "public_score": public,
                "private_score": private, "ranking_score": ranking_score(public, public, private, 1.0),
                "rejected": False, "competes": True, "stages_reached": ["static", "full"]}

    rows = [
        old("g000-c0001", 0, "human-seed", 1.0, 1.0),
        old("g001-c0002", 1, "rewrite", 3.0, 1.5),  # stored ranking score 1.5, at lambda = 1
        old("g001-c0003", 1, "rewrite", 2.0, 2.0),
    ]
    (run_dir / "runs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    driver = Driver(config, run_dir=run_dir)
    driver.resume()
    assert [c.id for c in driver.top_k(3)] == ["g001-c0002", "g001-c0003", "g000-c0001"]
    assert driver.by_id["g001-c0002"].fitness == pytest.approx(3.0)
    assert driver.grid.best is not None and driver.grid.best.id == "g001-c0002"

    penalised = replace(config, evaluate=replace(config.evaluate, holdout_penalty=1.0))
    again = Driver(penalised, run_dir=run_dir)
    again.resume()
    assert again.by_id["g001-c0002"].fitness == pytest.approx(1.5), "lambda = 1 set explicitly: as before"
    assert [c.id for c in again.top_k(3)] == ["g001-c0003", "g001-c0002", "g000-c0001"]


def test_a_resumed_runs_economics_see_the_replayed_order(mirror_project, tmp_path):
    """The stop rules read the economics series: after a resume it must rank as the
    archive does -- old lambda = 1 rows at the public score, a vetoed row not at all."""
    from evolvekit.search.driver import Driver

    config = load_config(mirror_project)
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    def old(cid: str, generation: int, operator: str, public: float, private: float) -> dict:
        return {"id": cid, "generation": generation, "operator": operator,
                "parent_id": None if generation == 0 else "g000-c0001",
                "block": f"def value():\n    return {public}\n", "score": public, "public_score": public,
                "private_score": private, "ranking_score": ranking_score(public, public, private, 1.0),
                "rejected": False, "competes": True, "stages_reached": ["static", "full"]}

    rows = [
        old("g000-c0001", 0, "human-seed", 1.0, 1.0),
        old("g001-c0002", 1, "rewrite", 3.0, 1.5),   # ranking 1.5 at lambda = 1, 3.0 now
        old("g001-c0003", 1, "rewrite", 10.0, 0.5),  # below the seed's hold-out: vetoed on replay
    ]
    (run_dir / "runs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    driver = Driver(config, run_dir=run_dir)
    driver.resume()
    points = driver._economics()
    assert points[-1].best == pytest.approx(3.0)


def test_the_run_summary_counts_what_the_hold_out_keeps_out_apart(mirror_project, tmp_path, monkeypatch, capsys):
    from evolvekit.cli import main
    from evolvekit.search.driver import Driver, RunSummary

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    rows = [
        {"id": "g000-c0001", "generation": 0, "operator": "human-seed", "block": "def value():\n    return 1\n",
         "score": 1.0, "public_score": 1.0, "private_score": -1.0, "rejected": False, "competes": True,
         "stages_reached": ["static", "full"]},
        # Finished, below the seed's hold-out: kept out, not unfinished.
        {"id": "g001-c0002", "generation": 1, "operator": "rewrite", "parent_id": "g000-c0001",
         "block": "def value():\n    return 2\n", "score": 2.0, "public_score": 2.0, "private_score": -2.0,
         "rejected": False, "competes": True, "stages_reached": ["static", "full"]},
        # Did not finish.
        {"id": "g001-c0003", "generation": 1, "operator": "rewrite", "parent_id": "g000-c0001",
         "block": "def value():\n    return 3\n", "score": -1000.0, "rejected": False, "competes": False,
         "last_failure": "stage full: exit code 1", "stages_reached": ["static"]},
    ]
    (run_dir / "runs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    def replay_only(self, generations=None):  # the summary of the run as it stands, with no search
        self.resume()
        return self._finalise(RunSummary())

    monkeypatch.setattr(Driver, "run", replay_only)
    assert main(["run", "--config", str(mirror_project), "--run-dir", str(run_dir), "--quiet"]) == 0
    out = capsys.readouterr().out
    assert "unfinished  : 1 (evaluated, never ranked)" in out
    assert "kept out    : 1 by the hold-out (finished, never ranked)" in out


# -- every view applies the veto as the replay does ---------------------------


def _view_rows() -> list[dict]:
    seed = _row("seed", -5.0, -5.0)
    seed["operator"] = "human-seed"
    seed["generation"] = 0
    seed["competes"] = True
    # Written before the veto existed: `competes: true`, no `veto` key.
    overfit = {**_row("overfit", -4.0, -6.0, penalty=0.0), "competes": True}
    honest = {**_row("honest", -4.5, -4.8, penalty=0.0), "competes": True}
    return [seed, overfit, honest]


def test_the_views_apply_the_veto_to_rows_written_before_it():
    rows = _view_rows()
    assert [r["id"] for r in rank(rows)] == ["honest", "seed"]
    assert competes(rows[1]) is True, "without the reference, only the stored fields"
    replayed = {r["id"]: r for r in replay_veto(rows)}
    assert competes(replayed["overfit"]) is False, "the replay applies the veto"
    assert replayed["overfit"]["veto"] == "holdout below the seed's -5"
    assert vetoed_count(rows) == 1 and unfinished_count(rows) == 0


def test_a_row_judged_with_the_veto_off_keeps_its_judgement():
    rows = _view_rows()
    rows[1]["veto"] = None  # written by a run with `holdout_veto: false`
    assert [r["id"] for r in rank(rows)] == ["overfit", "honest", "seed"]


def test_the_leaderboard_names_what_the_veto_keeps_out():
    rows = _view_rows()
    markdown = render_markdown(rows)
    assert "1 kept out by the hold-out" in markdown
    assert "- overfit ! score -4: holdout below the seed's -5" in markdown
    assert "did not finish the final stage" not in markdown
    page = render_html(rows)
    assert "overfit ! score -4: holdout below the seed&#x27;s -5" in page
    assert "<b>1</b><span>kept out by the hold-out</span>" in page


def test_the_seed_reference_is_the_last_seed_row_only():
    first = {"id": "a", "operator": "human-seed", "private_score": -1.0}
    again = {"id": "b", "operator": "human-seed", "private_score": None}
    assert seed_private([first]) == -1.0
    assert seed_private([first, again]) is None, "the last seed's hold-out failed: no reference"
    assert seed_private([again, first]) == -1.0
    assert seed_private([{"id": "c", "operator": "rewrite", "private_score": 3.0}]) is None


def test_a_seed_whose_hold_out_fails_clears_the_live_reference(mirror_project, tmp_path):
    config = load_config(mirror_project)
    cascade = Cascade(config, work_dir=tmp_path / "work")
    cascade.evaluate_generation([_value_candidate("g000-c0001", 1, "human-seed")])
    assert cascade.seed_private == pytest.approx(-1.0)
    broken = Candidate(id="g000-c0002", generation=0, block="def value():\n    return 'x'\n",
                       source=VALUE_SKELETON.format(v="'x'"), operator="human-seed")
    cascade.evaluate_generation([broken])
    assert cascade.seed_private is None


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


def test_the_hold_out_score_uses_the_hold_out_runs_own_kpis(mirror_project, tmp_path):
    """D6: a weighted KPI the private run did not report used to be taken from the public run."""
    config = load_config(mirror_project)
    score = replace(config.evaluate.score, weights={"value": 1.0, "bonus": 1.0})
    config = replace(config, evaluate=replace(config.evaluate, score=score))
    script = mirror_project.parent / "evaluate.py"
    script.write_text(
        script.read_text(encoding="utf-8").replace(
            "{'value': score}", "({'value': score, 'bonus': 5.0} if a.inputs == 'public' else {'value': score})"
        ),
        encoding="utf-8",
        newline="\n",
    )
    result = Cascade(config, work_dir=tmp_path / "work").evaluate_generation(
        [_value_candidate("g000-c0001", 1, "human-seed")]
    )["g000-c0001"]
    assert result.private_score is None and result.competes is False
    assert "the hold-out run did not report bonus" in (result.last_failure or "")


def test_the_hold_out_with_levels_must_report_every_level(mirror_project, tmp_path):
    from evolvekit.config import LevelConfig

    config = load_config(mirror_project)
    score = replace(
        config.evaluate.score,
        levels=(LevelConfig("value", "maximize", tolerance=0.1), LevelConfig("bonus", "maximize")),
    )
    config = replace(config, evaluate=replace(config.evaluate, score=score))
    script = mirror_project.parent / "evaluate.py"
    script.write_text(
        script.read_text(encoding="utf-8").replace(
            "{'value': score}", "({'value': score, 'bonus': 5.0} if a.inputs == 'public' else {'value': score})"
        ),
        encoding="utf-8",
        newline="\n",
    )
    result = Cascade(config, work_dir=tmp_path / "work").evaluate_generation(
        [_value_candidate("g000-c0001", 1, "human-seed")]
    )["g000-c0001"]
    assert result.private_score is None
    # The evaluator's own required-measure check may speak first; either way a
    # hold-out without a level fails with a sentence and is never scored from the public run.
    assert "bonus" in (result.last_failure or "")
