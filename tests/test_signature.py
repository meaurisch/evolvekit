"""The behaviour signature and the twin filter built on it.

The AST filter catches a child written differently. This one catches a child
that *decides* identically -- a monotone re-expression of best fit, which is
what five of the first real Phase B run's paid children turned out to be.
"""

from __future__ import annotations

import math
import random
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from evolvekit.candidate import SEED_OPERATOR, Candidate
from evolvekit.config import (
    DEFAULT_SIGNATURE_DIGITS,
    DEFAULT_SIGNATURE_IGNORE,
    build_config,
)
from evolvekit.evaluate.cascade import Cascade
from evolvekit.evaluate.signature import (
    Behaviour,
    BehaviourIndex,
    behaviour_signature,
    compared_numbers,
    round_significant,
    spread_factor,
    twin_margins,
    within,
)
from evolvekit.evaluate.types import StageOutcome

IGNORE = DEFAULT_SIGNATURE_IGNORE
DIGITS = DEFAULT_SIGNATURE_DIGITS


def sig(kpis, vectors=None, *, ignore=IGNORE, digits=DIGITS) -> str:
    return behaviour_signature(kpis, vectors, ignore=ignore, digits=digits)


# -- rounding --------------------------------------------------------------


@pytest.mark.parametrize(
    "value,digits,expected",
    [
        (5.871002923646529, 3, 5.87),
        (5.871002923646529, 9, 5.87100292),
        (0.000123456789, 4, 0.0001235),
        (-2144.0, 2, -2100.0),
        (0.0, 5, 0.0),
        (123.0, 1, 100.0),
    ],
)
def test_round_significant_counts_significant_digits(value, digits, expected):
    assert round_significant(value, digits) == pytest.approx(expected)


def test_round_significant_passes_non_finite_values_through():
    assert round_significant(float("inf"), 4) == float("inf")
    assert round_significant(float("nan"), 4) != round_significant(float("nan"), 4)


# -- the signature ---------------------------------------------------------


def test_the_signature_is_stable_and_order_independent():
    a = sig({"excess_pct": 5.871, "bins_used": 2144.0})
    b = sig({"bins_used": 2144.0, "excess_pct": 5.871})
    assert a == b == sig({"excess_pct": 5.871, "bins_used": 2144.0})
    assert len(a) == 16


def test_rounding_absorbs_float_noise_but_not_a_real_difference():
    base = {"excess_pct": 5.871002923646529}
    assert sig(base) == sig({"excess_pct": 5.871002923646530})
    assert sig(base) != sig({"excess_pct": 5.8710030})
    # Fewer digits is a coarser filter: at 3 digits the two collapse together.
    assert sig(base, digits=3) == sig({"excess_pct": 5.8710030}, digits=3)


def test_the_ignore_list_leaves_timing_and_size_out():
    fast = {"excess_pct": 5.871, "runtime_s": 0.0122, "complexity": 427.0}
    slow = {"excess_pct": 5.871, "runtime_s": 9.9, "complexity": 489.0}
    assert sig(fast) == sig(slow), "runtime and size are not behaviour"
    # ...and they are only ignored because the list says so.
    assert sig(fast, ignore=()) != sig(slow, ignore=())


def test_the_default_ignore_list_is_the_documented_one():
    assert DEFAULT_SIGNATURE_IGNORE == ("runtime_s", "complexity", "static_problems")
    assert DEFAULT_SIGNATURE_DIGITS == 9


def test_a_stage_that_reports_nothing_behavioural_has_no_signature():
    assert sig({"runtime_s": 1.0, "complexity": 400.0}) == ""
    assert sig({}) == ""


def test_list_valued_kpis_make_the_signature_per_instance():
    mean_only = {"excess_pct": 5.0}
    a = sig(mean_only, {"bins_per_instance": [48.0, 50.0, 43.0]})
    b = sig(mean_only, {"bins_per_instance": [50.0, 48.0, 43.0]})
    assert a != b, "two different packings collapsed onto one signature"
    assert a == sig(mean_only, {"bins_per_instance": [48.0, 50.0, 43.0]})
    # A scalars-only evaluator is unaffected -- the contract stays compatible.
    assert sig(mean_only) == sig(mean_only, {})


# -- the index -------------------------------------------------------------


def test_the_index_is_keyed_by_stage_as_well_as_signature():
    index = BehaviourIndex()
    signature = sig({"excess_pct": 5.871})
    index.add("g000-c0001", "proxy", signature)
    assert index.check("proxy", signature).twin_id == "g000-c0001"
    assert index.check("full", signature).twin_id is None


def test_the_index_keeps_the_first_id_for_a_signature():
    index = BehaviourIndex()
    signature = sig({"excess_pct": 5.871})
    index.add("first", "proxy", signature)
    index.add("second", "proxy", signature)
    assert index.check("proxy", signature).twin_id == "first"


def test_an_empty_signature_is_never_a_twin():
    index = BehaviourIndex()
    index.add("a", "proxy", "")
    assert index.check("proxy", "").twin_id is None
    assert index.by_key == {}


def test_the_verdict_names_the_twin_and_the_stage():
    index = BehaviourIndex()
    signature = sig({"excess_pct": 5.871})
    index.add("g000-c0001", "proxy", signature)
    verdict = index.check("proxy", signature)
    assert verdict.duplicate
    assert verdict.reason == "behavioural duplicate of g000-c0001 at stage proxy"


# -- the cascade -----------------------------------------------------------

SKELETON = """\
# EVOLVE-BLOCK-START
def rule():
    return 1
# EVOLVE-BLOCK-END
"""

EVALUATOR = """\
import argparse, json, pathlib
parser = argparse.ArgumentParser()
parser.add_argument("--candidate")
parser.add_argument("--inputs")
parser.add_argument("--out")
args = parser.parse_args()
src = pathlib.Path(args.candidate).read_text(encoding="utf-8")
# Every candidate that mentions `scale` behaves like the seed: the number it
# reports is identical, the block that produced it is not.
value = 7.0 if "worse" in src else 3.0
pathlib.Path(args.out).write_text(
    json.dumps(
        {
            "kpis": {
                "value": value,
                "runtime_s": 0.5,
                # Not behaviour: it tracks how the block is written, not what
                # it decided. Two twins differ here and nowhere else.
                "block_chars": float(len(src)),
                "per_item": [value, 1.0],
            }
        }
    ),
    encoding="utf-8",
)
"""


@pytest.fixture
def two_stage_config(tmp_path: Path):
    (tmp_path / "skeleton.py").write_text(SKELETON, encoding="utf-8")
    (tmp_path / "evaluate.py").write_text(EVALUATOR, encoding="utf-8")
    command = "python evaluate.py --candidate {candidate} --inputs {inputs} --out {out}"
    raw = {
        "problem": {"skeleton": "skeleton.py"},
        "evaluate": {
            "stages": [
                {"id": "static", "kind": "builtin-static", "import_check": False},
                {"id": "proxy", "kind": "command", "command": command, "timeout": 60},
                {"id": "full", "kind": "command", "command": command, "timeout": 60},
            ],
            "score": {"objective": "value"},
            # `block_chars` tracks how the block is written, not what it
            # decided, so it belongs on the ignore list beside `complexity`.
            "signature_ignore": [
                "runtime_s",
                "complexity",
                "static_problems",
                "block_chars",
            ],
        },
        "models": {
            "small": {"provider": "fake", "model": "s", "options": {"responses": ["x"]}},
            "strong": {"provider": "fake", "model": "b", "options": {"responses": ["x"]}},
        },
    }
    return build_config(raw, base_dir=tmp_path)


def _twin_pair() -> list[Candidate]:
    """Two blocks that are written differently and decide identically."""
    return [
        candidate("a", "def rule():\n    return 1"),
        candidate("b", "def rule():\n    return 1 * 1"),
    ]


def candidate(cid: str, body: str) -> Candidate:
    source = SKELETON.replace("def rule():\n    return 1", body)
    return Candidate(id=cid, generation=0, block=body, source=source, operator="rewrite")


def test_a_twin_is_stopped_at_the_stage_that_caught_it(two_stage_config, tmp_path):
    index = BehaviourIndex()
    cascade = Cascade(
        two_stage_config, work_dir=tmp_path / "work", signatures=index
    )
    seed = candidate("seed", "def rule():\n    return 1")
    results = cascade.evaluate_generation([seed])
    assert results["seed"].stages_reached == ["static", "proxy", "full"]
    assert not results["seed"].rejected

    # Structurally different, numerically identical: caught at the proxy stage,
    # and therefore never charged for the full one.
    twin = candidate("twin", "def rule():\n    return 1 * 1")
    results = cascade.evaluate_generation([twin])
    result = results["twin"]
    assert result.behaviour_twin_id == "seed"
    assert result.rejected is True
    assert result.reject_reason == "behavioural duplicate of seed at stage proxy"
    assert result.stages_reached == ["static", "proxy"], "the twin bought a full eval"
    # The KPIs and the score survive for the record.
    assert result.kpis["value"] == 3.0
    assert result.score == pytest.approx(3.0)


def test_the_seed_is_a_valid_twin_target(two_stage_config, tmp_path):
    index = BehaviourIndex()
    cascade = Cascade(two_stage_config, work_dir=tmp_path / "work", signatures=index)
    cascade.evaluate_generation([candidate("seed", "def rule():\n    return 1")])
    twin = candidate("child", "def rule():\n    return 3 - 2")
    assert cascade.evaluate_generation([twin])["child"].behaviour_twin_id == "seed"


def test_two_twins_in_one_generation_only_cost_one_evaluation(
    two_stage_config, tmp_path
):
    cascade = Cascade(two_stage_config, work_dir=tmp_path / "work")
    results = cascade.evaluate_generation(
        [
            candidate("a", "def rule():\n    return 1"),
            candidate("b", "def rule():\n    return 1 * 1"),
        ]
    )
    assert results["a"].behaviour_twin_id is None
    assert results["b"].behaviour_twin_id == "a"


def test_a_genuinely_different_behaviour_is_not_a_twin(two_stage_config, tmp_path):
    cascade = Cascade(two_stage_config, work_dir=tmp_path / "work")
    results = cascade.evaluate_generation(
        [
            candidate("a", "def rule():\n    return 1"),
            candidate("b", "def rule():\n    return 1  # worse"),
        ]
    )
    assert results["b"].behaviour_twin_id is None
    assert results["b"].stages_reached == ["static", "proxy", "full"]
    assert results["a"].behaviour_signatures != results["b"].behaviour_signatures


def test_the_static_stage_is_never_fingerprinted(two_stage_config, tmp_path):
    cascade = Cascade(two_stage_config, work_dir=tmp_path / "work")
    result = cascade.evaluate_generation(
        [candidate("a", "def rule():\n    return 1")]
    )["a"]
    assert set(result.behaviour_signatures) == {"proxy", "full"}


# -- search.novelty.behavioural: off | auto ---------------------------------


def _with_behavioural(config, mode: str):
    return replace(
        config,
        search=replace(
            config.search,
            novelty=replace(config.search.novelty, behavioural=mode),
        ),
    )


def test_behavioural_off_never_fingerprints_or_catches_a_twin(
    two_stage_config, tmp_path
):
    off_config = _with_behavioural(two_stage_config, "off")
    cascade = Cascade(off_config, work_dir=tmp_path / "work")
    cascade.evaluate_generation([candidate("seed", "def rule():\n    return 1")])
    twin = candidate("twin", "def rule():\n    return 1 * 1")
    result = cascade.evaluate_generation([twin])["twin"]
    assert result.behaviour_twin_id is None
    assert result.behaviour_signatures == {}
    assert result.stages_reached == ["static", "proxy", "full"], "not stopped early"


@pytest.fixture
def stochastic_stage_config(tmp_path: Path):
    """One deterministic stage and one stochastic (`seeds: 2`) stage, both
    reporting the seed's exact KPIs regardless of the block -- so a genuine
    behavioural filter would catch every candidate as a twin of the seed."""
    (tmp_path / "skeleton.py").write_text(SKELETON, encoding="utf-8")
    script = tmp_path / "same_every_time.py"
    script.write_text(
        "import json, pathlib, sys\n"
        "out, seed = sys.argv[1], sys.argv[2]\n"
        "pathlib.Path(out).write_text(json.dumps({'kpis': {'value': 3.0}}))\n",
        encoding="utf-8",
    )
    command = f'"{sys.executable}" "{script}" {{out}} {{seed}} {{candidate}}'
    raw = {
        "problem": {"skeleton": "skeleton.py"},
        "evaluate": {
            "stages": [
                {"id": "static", "kind": "builtin-static", "import_check": False},
                {
                    "id": "full",
                    "kind": "command",
                    "command": command,
                    "timeout": 60,
                    "seeds": 2,
                },
            ],
            "score": {"objective": "value"},
        },
        "models": {
            "small": {"provider": "fake", "model": "s", "options": {"responses": ["x"]}},
            "strong": {"provider": "fake", "model": "b", "options": {"responses": ["x"]}},
        },
    }
    return build_config(raw, base_dir=tmp_path)


def test_behavioural_auto_skips_a_stochastic_stage_while_it_knows_no_noise(stochastic_stage_config, tmp_path):
    """No seed, so nothing is run again: the stage's repeatability stays
    unknown and its noise 0, and `auto` does not check it. What it saw is
    still kept, so that a later check has something to compare with."""
    auto_config = _with_behavioural(stochastic_stage_config, "auto")
    cascade = Cascade(auto_config, work_dir=tmp_path / "work")
    cascade.evaluate_generation([candidate("first", "def rule():\n    return 1")])
    assert cascade.noise.repeatable("full") is None
    child = candidate("child", "def rule():\n    return 1 * 1")
    result = cascade.evaluate_generation([child])["child"]
    assert result.behaviour_twin_id is None
    assert result.stages_reached == ["static", "full"]


def test_behavioural_on_still_catches_the_same_stochastic_stage(
    stochastic_stage_config, tmp_path
):
    """`on` (the default) is unchanged: `auto`'s skip is opt-in."""
    cascade = Cascade(stochastic_stage_config, work_dir=tmp_path / "work")
    cascade.evaluate_generation([candidate("seed", "def rule():\n    return 1")])
    child = candidate("child", "def rule():\n    return 1 * 1")
    result = cascade.evaluate_generation([child])["child"]
    assert result.behaviour_twin_id == "seed"


def test_a_size_like_kpi_hides_a_twin_until_it_is_ignored(two_stage_config, tmp_path):
    """The whole reason `evaluate.signature_ignore` exists.

    The evaluator reports `block_chars`, which differs between two blocks that
    decide identically. Left in the signature it makes every twin look novel;
    named on the ignore list, the twin is caught.
    """
    leaky = replace(
        two_stage_config,
        evaluate=replace(
            two_stage_config.evaluate, signature_ignore=DEFAULT_SIGNATURE_IGNORE
        ),
    )
    assert "block_chars" not in DEFAULT_SIGNATURE_IGNORE
    cascade = Cascade(leaky, work_dir=tmp_path / "leaky")
    assert cascade.evaluate_generation(_twin_pair())["b"].behaviour_twin_id is None

    assert "block_chars" in two_stage_config.evaluate.signature_ignore
    tightened = Cascade(two_stage_config, work_dir=tmp_path / "tight")
    assert tightened.evaluate_generation(_twin_pair())["b"].behaviour_twin_id == "a"


# -- twins by the run's noise (A2.2) -----------------------------------------


def test_the_spread_factor_for_one_number_is_the_designs_two_sigma_rule():
    assert spread_factor(1) == pytest.approx(math.sqrt(2))
    margins = twin_margins(Behaviour({"cost": 100.0}, {}), {"cost": 1.0}, {}, seeds=1)
    assert margins["cost"] == pytest.approx(2.0), "2 sigma / sqrt(seeds)"


def test_the_spread_factor_grows_with_the_count_so_all_of_them_hold_together():
    """`c_m` is chosen so that m independent numbers are all within it with
    probability erf(1): P(|Z| <= c_m)^m = 0.8427."""
    for m in (1, 2, 10, 40):
        assert math.erf(spread_factor(m) / math.sqrt(2)) ** m == pytest.approx(math.erf(1.0))
    assert spread_factor(1) < spread_factor(2) < spread_factor(10)


def test_a_small_noise_tells_apart_what_three_digits_could_not():
    """(Corrected from the design, open question 2.) With one run varying by 0.1 %,
    0.3 % apart is two candidates and 0.1 % apart is one: three significant digits
    called both pairs twins."""
    margins = twin_margins(Behaviour({"cost": 100.0}, {}), {"cost": 0.1}, {}, seeds=1)  # margin 0.2
    assert not within(Behaviour({"cost": 100.0}, {}), Behaviour({"cost": 100.3}, {}), margins, 9)
    assert within(Behaviour({"cost": 100.0}, {}), Behaviour({"cost": 100.1}, {}), margins, 9)
    assert round_significant(100.0, 3) == round_significant(100.3, 3), "what the old rule saw"


def test_a_large_noise_calls_twins_what_three_digits_told_apart():
    margins = twin_margins(Behaviour({"cost": 100.0}, {}), {"cost": 2.0}, {}, seeds=3)  # 2.31
    assert margins["cost"] == pytest.approx(2 * 2.0 / math.sqrt(3))
    assert within(Behaviour({"cost": 100.0}, {}), Behaviour({"cost": 101.5}, {}), margins, 9)
    assert round_significant(100.0, 3) != round_significant(101.5, 3), "what the old rule saw"


def test_a_deterministic_kpi_is_compared_exactly_beside_a_noisy_one():
    a = Behaviour({"cost": 100.0, "vehicles": 7.0}, {})
    margins = twin_margins(a, {"cost": 1.0, "vehicles": 0.0}, {}, seeds=1)
    assert margins["vehicles"] == 0.0
    assert margins["cost"] == pytest.approx(2.0), "one noisy number: m = 1"
    assert within(a, Behaviour({"cost": 101.0, "vehicles": 7.0}, {}), margins, 9)
    assert not within(a, Behaviour({"cost": 100.0, "vehicles": 8.0}, {}), margins, 9)
    assert not within(a, Behaviour({"cost": 100.0}, {}), margins, 9), "a measure missing on one side"
    assert not within(a, Behaviour({"cost": 100.0, "vehicles": 7.0}, {"cost_per_instance": [1.0]}), margins, 9)


def test_vectors_of_another_length_are_not_twins():
    a = Behaviour({}, {"cost_per_instance": [1.0, 2.0]})
    b = Behaviour({}, {"cost_per_instance": [1.0, 2.0, 3.0]})
    assert not within(a, b, {"cost_per_instance": [5.0, 5.0]}, 9)


def test_one_representation_of_each_measure_is_compared():
    """Decision 3: on a per-instance stage the raw per-instance vectors, nothing derived."""
    outcome = StageOutcome(stage_id="full", ok=True, kpis={"cost": 95.0, "cost_raw": 120.0, "runtime_s": 3.0},
                           vector_kpis={"cost_per_instance": [100.0, 140.0], "cost_pct_of_baseline": [90.0, 100.0],
                                        "runtime_s_per_instance": [1.0, 2.0]})
    seen = compared_numbers(outcome, ignore=("runtime_s",), per_instance=True, objective="cost")
    assert seen.kpis == {} and set(seen.vectors) == {"cost_per_instance"}
    assert seen.vectors["cost_per_instance"] == [100.0, 140.0]


def test_without_instances_every_scalar_and_list_the_evaluator_reported_is_compared():
    outcome = StageOutcome(stage_id="full", ok=True,
                           kpis={"cost": 95.0, "cost_raw": 120.0, "runtime_s": 3.0, "vehicles": 7.0},
                           vector_kpis={"per_item": [1.0, 2.0], "runtime_s": [0.1]})
    seen = compared_numbers(outcome, ignore=("runtime_s",), per_instance=False, objective="cost")
    assert seen.kpis == {"cost": 95.0, "vehicles": 7.0}
    assert seen.vectors == {"per_item": [1.0, 2.0]}


def test_an_unknown_element_falls_back_to_the_relative_noise():
    margins = twin_margins(Behaviour({}, {"cost_per_instance": [100.0, 200.0]}), {},
                           {"cost_per_instance": [1.0, None]}, seeds=1, cvs={"cost_per_instance": 0.01})
    c = spread_factor(2)
    assert margins["cost_per_instance"] == pytest.approx([c * 1.0 * math.sqrt(2), c * 2.0 * math.sqrt(2)])


def test_a_number_with_no_noise_known_at_all_is_compared_exactly():
    margins = twin_margins(Behaviour({"cost": 1.0}, {"cost_per_instance": [100.0, 200.0]}), {},
                           {"cost_per_instance": [1.0, None]}, seeds=1)
    assert margins["cost"] == 0.0
    assert margins["cost_per_instance"] == pytest.approx([math.sqrt(2) * math.sqrt(2), 0.0]), "m = 1"


def test_a_true_twin_is_caught_in_about_84_percent():
    """One noisy number, seeds 1, sd 1: the difference is N(0, 2), the margin 2:
    P(|Z| <= sqrt 2) = erf(1) = 84.27 %. 20 000 trials: SE 0.26 %."""
    rng, caught = random.Random(11), 0
    margins = twin_margins(Behaviour({"cost": 0.0}, {}), {"cost": 1.0}, {}, seeds=1)  # 2.0
    for _ in range(20_000):
        caught += within(Behaviour({"cost": rng.gauss(0, 1)}, {}), Behaviour({"cost": rng.gauss(0, 1)}, {}), margins, 9)
    print(f"true twin caught: {caught / 20_000:.2%}")
    assert 0.825 <= caught / 20_000 <= 0.86


def test_a_real_gain_of_twice_the_margin_is_called_a_twin_in_about_8_percent():
    """Difference N(4, 2), margin 2: Phi(-sqrt 2) - Phi(-3 sqrt 2) = 7.87 %.
    20 000 trials: SE 0.19 %; measured 7.60 % (seed 15), so the band is [7.0, 8.8] %."""
    rng, caught = random.Random(15), 0
    margins = twin_margins(Behaviour({"cost": 0.0}, {}), {"cost": 1.0}, {}, seeds=1)  # 2.0
    for _ in range(20_000):
        caught += within(Behaviour({"cost": rng.gauss(0, 1)}, {}), Behaviour({"cost": rng.gauss(4, 1)}, {}), margins, 9)
    print(f"a gain of four noise sd called a twin: {caught / 20_000:.2%}")
    assert 0.070 <= caught / 20_000 <= 0.088


def test_with_ten_instances_a_true_twin_is_still_caught_in_about_84_percent():
    """Every element within c_10 * sigma * sqrt 2: P = erf(1) by construction.
    Per element at the design's 2 sigma it would be 0.8427**10 = 18.1 %."""
    rng = random.Random(12)
    base = Behaviour({}, {"cost_per_instance": [100.0] * 10})
    margins = twin_margins(base, {}, {"cost_per_instance": [1.0] * 10}, seeds=1)
    design = {"cost_per_instance": [2.0] * 10}
    caught = old = 0
    for _ in range(20_000):
        a = Behaviour({}, {"cost_per_instance": [rng.gauss(0, 1) for _ in range(10)]})
        b = Behaviour({}, {"cost_per_instance": [rng.gauss(0, 1) for _ in range(10)]})
        caught += within(a, b, margins, 9)
        old += within(a, b, design, 9)
    print(f"ten numbers: caught {caught / 20_000:.2%}; at 2 sigma each {old / 20_000:.2%}")
    assert 0.825 <= caught / 20_000 <= 0.86
    assert 0.165 <= old / 20_000 <= 0.195


def test_a_twin_is_only_of_an_earlier_candidate_that_scored_at_least_as_well():
    """Decision 2: within the noise of two entries, the child is a twin of the better
    of those that scored at least as well; ahead of all of them it is not a twin."""
    index = BehaviourIndex()
    index.add("lo", "full", "h1", Behaviour({"cost": 100.0}, {}, score=-100.0))
    index.add("hi", "full", "h2", Behaviour({"cost": 99.0}, {}, score=-99.0))
    margins = twin_margins(Behaviour({"cost": 0.0}, {}), {"cost": 1.0}, {}, seeds=1)  # 2.0
    assert index.check_values("full", Behaviour({"cost": 99.5}, {}, score=-99.5), margins, 9).twin_id == "hi"
    assert index.check_values("full", Behaviour({"cost": 98.5}, {}, score=-98.5), margins, 9).twin_id is None
    assert index.check_values("proxy", Behaviour({"cost": 99.5}, {}, score=-99.5), margins, 9).twin_id is None


def test_among_equal_scores_the_twin_is_the_first_one_seen():
    index = BehaviourIndex()
    index.add("first", "full", "h1", Behaviour({"cost": 100.0}, {}, score=1.0))
    index.add("second", "full", "h2", Behaviour({"cost": 100.5}, {}, score=1.0))
    verdict = index.check_values("full", Behaviour({"cost": 100.2}, {}, score=1.0), {"cost": 2.0}, 9)
    assert verdict.twin_id == "first"
    assert verdict.reason == "behavioural duplicate of first at stage full"


def test_an_entry_without_values_is_only_a_hash():
    index = BehaviourIndex()
    index.add("legacy", "full", "h1")
    assert index.check("full", "h1").twin_id == "legacy"
    assert index.check_values("full", Behaviour({"cost": 1.0}, {}, score=0.0), {"cost": 2.0}, 9).twin_id is None


def _converged(rng, child_mean, *, entries=50, seeds=3, entry_sd=0.01):
    """50 near-equal entries (stored scores 0 +- `entry_sd`) and one child whose score is
    a mean of `seeds` runs of sd 1 around `child_mean`; the compared number is the score itself."""
    index = BehaviourIndex()
    for i in range(entries):
        value = rng.gauss(0.0, entry_sd)
        index.add(f"e{i}", "full", f"h{i}", Behaviour({"score": value}, {}, score=value))
    x = rng.gauss(child_mean, 1 / math.sqrt(seeds))
    margins = twin_margins(Behaviour({"score": x}, {}), {"score": 1.0}, {}, seeds=seeds)
    return index.check_values("full", Behaviour({"score": x}, {}, score=x), margins, 9).twin_id


def test_in_a_converged_index_a_child_one_run_sd_better_is_not_a_twin_in_over_90_percent():
    """seeds 3: the child's score is N(1, 1/3); it is a twin only when it falls below
    the entries (about 0): Phi(-1.73) = 4.2 %, so not a twin in about 95 %.
    seeds 1: N(1, 1), a twin when in [-2, 0]: about 16 %, not a twin in about 84 %.
    20 000 trials: SE 0.15 % and 0.26 %. With entries that carry their own noise
    (stored scores N(0, 1/3), the lucky best of 50 sits near 0.75) the child is a
    twin far more often: printed, not asserted (Decision 13: that is selection on
    measured scores, not the twin rule)."""
    rng = random.Random(13)
    not_twin = sum(_converged(rng, 1.0) is None for _ in range(20_000)) / 20_000
    rng = random.Random(16)
    not_twin_1 = sum(_converged(rng, 1.0, seeds=1) is None for _ in range(20_000)) / 20_000
    rng = random.Random(17)
    noisy = sum(_converged(rng, 1.0, entry_sd=1 / math.sqrt(3)) is not None for _ in range(20_000)) / 20_000
    print(f"not a twin: seeds 3 {not_twin:.2%}, seeds 1 {not_twin_1:.2%}; "
          f"with noisy entries a twin in {noisy:.2%}")
    assert not_twin >= 0.90
    assert not_twin_1 >= 0.80


def test_in_a_converged_index_an_equal_child_is_a_twin_in_about_half():
    """The child is a twin when its score is at most the entries' (and within the
    margin, 1.15): about 49 %. SE 0.35 %."""
    rng = random.Random(14)
    rate = sum(_converged(rng, 0.0) is not None for _ in range(20_000)) / 20_000
    print(f"an equal child is a twin in {rate:.2%}")
    assert 0.46 <= rate <= 0.53


# -- in the cascade: a wall-clock evaluator and a repeatable one ---------------

WALL_CLOCK = """\
import json, pathlib, re, sys
out, seed, cand = sys.argv[1], int(sys.argv[2]), sys.argv[3]
src = pathlib.Path(cand).read_text(encoding="utf-8")
found = re.search(r"offset=(-?[0-9.]+)", src)
offset = float(found.group(1)) if found else 0.0
# Fresh noise every run, the same program and seed included -- what a solver
# bounded by wall-clock time does. Deterministic here so the test is: seed 0
# and seed 1 move the value by +-0.01 (one run's sd about 0.014), and a
# counter makes every run, a repeat of seed 0 too, a little different.
counter = pathlib.Path(__file__).with_suffix(".count")
n = int(counter.read_text()) if counter.exists() else 0
counter.write_text(str(n + 1))
value = 3.0 + offset + (0.01 if seed % 2 == 0 else -0.01) + 1e-7 * n
pathlib.Path(out).write_text(json.dumps({"kpis": {"value": value}}), encoding="utf-8")
"""

REPEATABLE = """\
import json, pathlib, re, sys
out, seed, cand = sys.argv[1], int(sys.argv[2]), sys.argv[3]
src = pathlib.Path(cand).read_text(encoding="utf-8")
found = re.search(r"offset=(-?[0-9.]+)", src)
offset = float(found.group(1)) if found else 0.0
# The same program on the same seed gives the same number. A child's offset
# counts twice on even seeds and against it on odd ones: its mean moves by
# offset/2 and its paired difference by 3*offset -- within that noise, yet
# not the same behaviour.
value = 3.0 + 0.01 * seed + (2 * offset if seed % 2 == 0 else -offset)
pathlib.Path(out).write_text(json.dumps({"kpis": {"value": value}}), encoding="utf-8")
"""


QUIET_WALL_CLOCK = WALL_CLOCK.replace("(0.01 if seed % 2 == 0 else -0.01)", "(0.001 if seed % 2 == 0 else -0.001)")
"""The same, ten times quieter: one run's sd about 0.0014, a margin about 0.002."""
assert QUIET_WALL_CLOCK != WALL_CLOCK


def _noise_config(tmp_path: Path, script_text: str, *, behavioural: str = "on", digits: int | None = None,
                  direction: str | None = None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "skeleton.py").write_text(SKELETON, encoding="utf-8")
    script = tmp_path / "evaluator.py"
    script.write_text(script_text, encoding="utf-8")
    command = f'"{sys.executable}" "{script}" {{out}} {{seed}} {{candidate}}'
    raw = {
        "problem": {"skeleton": "skeleton.py"},
        "evaluate": {
            "stages": [
                {"id": "static", "kind": "builtin-static", "import_check": False},
                {"id": "full", "kind": "command", "command": command, "timeout": 60, "seeds": 2},
            ],
            "score": {"objective": "value"},
        },
        "search": {"novelty": {"behavioural": behavioural}},
        "models": {
            "small": {"provider": "fake", "model": "s", "options": {"responses": ["x"]}},
            "strong": {"provider": "fake", "model": "b", "options": {"responses": ["x"]}},
        },
    }
    if digits is not None:
        raw["evaluate"]["signature_digits"] = digits
    if direction is not None:
        raw["evaluate"]["score"]["direction"] = direction
    return build_config(raw, base_dir=tmp_path)


def _seed() -> Candidate:
    body = "def rule():\n    return 1"
    return Candidate(id="seed", generation=0, block=body, source=SKELETON, operator=SEED_OPERATOR)


def _offset(cid: str, offset: str, *, written: str = "1") -> Candidate:
    return candidate(cid, f"def rule():\n    return {written}  # offset={offset}")


def _after_the_seed(tmp_path: Path, script_text: str, child: Candidate, **kw):
    cascade = Cascade(_noise_config(tmp_path, script_text, **kw), work_dir=tmp_path / "work")
    assert not cascade.evaluate_generation([_seed()])["seed"].rejected
    return cascade, cascade.evaluate_generation([child])[child.id]


def test_a_child_within_the_noise_of_the_seed_and_no_better_is_its_twin(tmp_path):
    cascade, result = _after_the_seed(tmp_path, WALL_CLOCK, _offset("child", "-0.001"))
    assert cascade.noise.repeatable("full") is False
    assert cascade.noise.sd("full", "value") > 0.01
    assert result.behaviour_twin_id == "seed"
    assert result.reject_reason == "behavioural duplicate of seed at stage full"
    values = result.behaviour_values["full"]
    assert values["kpis"] == {"value": pytest.approx(2.999, abs=1e-5)}
    assert values["score"] == pytest.approx(result.stage_scores["full"])


def test_a_child_within_the_noise_but_ahead_of_the_seed_is_evaluated(tmp_path):
    _, result = _after_the_seed(tmp_path, WALL_CLOCK, _offset("child", "0.001"))
    assert result.behaviour_twin_id is None
    assert result.competes


def test_a_child_beyond_the_noise_is_evaluated_further(tmp_path):
    _, result = _after_the_seed(tmp_path, WALL_CLOCK, _offset("child", "-0.1"))
    assert result.behaviour_twin_id is None
    assert result.competes


def test_on_a_minimising_goal_at_least_as_good_means_at_most_as_high(tmp_path):
    """`direction: minimize`: the score is `-value`, so a child whose value is
    higher than the seed's within the noise did no better and is its twin, and
    one lower within the noise is ahead and is evaluated."""
    _, worse = _after_the_seed(tmp_path / "worse", WALL_CLOCK, _offset("child", "0.001"), direction="minimize")
    assert worse.behaviour_twin_id == "seed"
    _, better = _after_the_seed(tmp_path / "better", WALL_CLOCK, _offset("child", "-0.001"), direction="minimize")
    assert better.behaviour_twin_id is None
    assert better.competes


def test_the_noise_rule_compares_the_numbers_not_a_coarse_rounding_of_them(tmp_path):
    """`signature_digits: 3` rounds the numbers compared exactly, not the
    noisy ones: 0.004 apart under a margin of about 0.002 is two candidates,
    though at three digits both read 3.00. What is kept has nine digits."""
    config = _noise_config(tmp_path, QUIET_WALL_CLOCK, digits=3)
    cascade = Cascade(config, work_dir=tmp_path / "work")
    cascade.evaluate_generation([candidate("first", "def rule():\n    return 1")])
    assert cascade.noise.repeatable("full") is None and cascade.noise.sd("full", "value") > 0
    apart = cascade.evaluate_generation([_offset("apart", "-0.004")])["apart"]
    assert apart.behaviour_twin_id is None
    assert round_significant(apart.kpis["value"], 3) == round_significant(3.0, 3), "what three digits would see"
    assert apart.behaviour_values["full"]["kpis"]["value"] == pytest.approx(2.996, abs=1e-6)
    close = cascade.evaluate_generation([_offset("close", "-0.001")])["close"]
    assert close.behaviour_twin_id == "first", "the noise rule is the one in force"


def test_on_a_repeatable_stage_twins_are_exact_at_nine_digits(tmp_path):
    """The child's mean is 0.0005 below the seed's and its paired noise sd
    0.0015 (margin 0.0021): the noise rule would call it the seed's twin, the
    exact rule on a repeatable stage does not."""
    cascade, result = _after_the_seed(tmp_path, REPEATABLE, _offset("child", "-0.001"))
    assert cascade.noise.repeatable("full") is True
    sd = cascade.noise.sd("full", "value")
    assert sd == pytest.approx(0.0015)
    seed_value, child_value = 3.005, result.kpis["value"]
    noise_says = within(Behaviour({"value": child_value}, {}), Behaviour({"value": seed_value}, {}),
                        twin_margins(Behaviour({"value": child_value}, {}), {"value": sd}, {}, seeds=2), 9)
    assert noise_says and child_value < seed_value, "the paired noise would call it a twin"
    assert result.behaviour_twin_id is None
    twin = cascade.evaluate_generation([_offset("again", "0", written="1 * 1")])["again"]
    assert twin.behaviour_twin_id == "seed"


def test_with_no_noise_twins_are_exact_at_nine_digits(stochastic_stage_config, tmp_path):
    cascade = Cascade(stochastic_stage_config, work_dir=tmp_path / "work")
    cascade.evaluate_generation([candidate("first", "def rule():\n    return 1")])
    assert cascade.noise.sd("full", "value") == 0.0
    result = cascade.evaluate_generation([candidate("child", "def rule():\n    return 1 * 1")])["child"]
    assert result.behaviour_twin_id == "first"
    assert result.behaviour_values["full"]["kpis"] == {"value": 3.0}


def test_behavioural_auto_checks_a_stochastic_stage_once_it_knows_the_noise(stochastic_stage_config, tmp_path):
    """Run again, the seed says the stage is repeatable: `auto` checks it from
    then on, against what it kept of the seed. A wall-clock stage has its noise
    from the seed's own runs and is checked from the first child."""
    auto_config = _with_behavioural(stochastic_stage_config, "auto")
    cascade = Cascade(auto_config, work_dir=tmp_path / "work")
    cascade.evaluate_generation([_seed()])
    assert cascade.noise.repeatable("full") is True
    child = cascade.evaluate_generation([candidate("child", "def rule():\n    return 1 * 1")])["child"]
    assert child.behaviour_twin_id == "seed"

    _, noisy = _after_the_seed(tmp_path / "wall", WALL_CLOCK, _offset("child", "-0.001"), behavioural="auto")
    assert noisy.behaviour_twin_id == "seed"


def test_a_resumed_run_compares_with_the_values_its_rows_kept(two_stage_config, tmp_path):
    from evolvekit.search.driver import Driver

    def row(cid, value, **extra):
        c = Candidate(id=cid, generation=0, block="def rule():\n    return 1", source="",
                      operator=SEED_OPERATOR if cid == "seed" else "rewrite", score=value,
                      stages_reached=["static", "proxy", "full"], **extra)
        c.behaviour_signatures = {"full": f"h-{cid}"}
        c.behaviour_values = {"full": {"kpis": {"value": value}, "vectors": {}, "score": value}}
        return c.to_record()

    first = Driver(two_stage_config, run_dir=tmp_path / "run")
    first.ledger.record_run(row("seed", 3.0))
    caught = row("twin", 2.995, rejected=True, novelty="behavioural", behaviour_twin_id="seed")
    first.ledger.record_run(caught)
    legacy = row("legacy", 9.0)
    del legacy["behaviour_values"]
    first.ledger.record_run(legacy)

    resumed = Driver(two_stage_config, run_dir=tmp_path / "run")
    resumed.resume()
    index = resumed.behaviour
    verdict = index.check_values("full", Behaviour({"value": 2.999}, {}, score=2.999), {"value": 0.02}, 9)
    assert verdict.twin_id == "seed"
    assert index.values["full"]["seed"].score == 3.0
    assert "twin" not in index.values["full"], "a twin was not a target where it was caught"
    assert index.check("full", "h-twin").twin_id is None, "nor was its hash"
    assert "legacy" not in index.values["full"]
    assert index.check("full", "h-legacy").twin_id == "legacy"


def test_a_resumed_run_rebuilds_the_index_the_live_run_built(tmp_path):
    """Two generations of a run on a wall-clock stage -- a child ahead of the
    seed within its noise, then one no better than it (a noise twin, whose hash
    is its own) -- and the run resumed from its rows: the same hashes, the same
    compared numbers and scores."""
    from evolvekit.providers.fake import FakeProvider
    from evolvekit.search.driver import Driver

    config = _noise_config(tmp_path, WALL_CLOCK)
    config = replace(
        config,
        search=replace(
            config.search, generations=2, children_per_generation=1, adaptive_children=None,
            big_step_every=99, scratchpad_every=0, inspirations=0, operators={"rewrite": 1.0},
        ),
        stop=replace(config.stop, patience=99),
    )
    blocks = ['def rule():\n    return "offset=0.001" and 1\n', 'def rule():\n    return "offset=-0.001" and 1\n']
    provider = FakeProvider([f"```python\n{b}```" for b in blocks], cycle=True)
    live = Driver(config, run_dir=tmp_path / "run", providers={"small": provider, "strong": provider})
    live.run()
    rows = live.ledger.runs()
    assert any(r.get("behaviour_twin_id") for r in rows), "the second child is a noise twin"

    resumed = Driver(config, run_dir=tmp_path / "run",
                     providers={"small": FakeProvider(["x"]), "strong": FakeProvider(["x"])})
    resumed.resume()
    assert set(live.behaviour.values["full"]) == {r["id"] for r in rows if not r.get("behaviour_twin_id")}
    assert resumed.behaviour.by_key == live.behaviour.by_key
    assert resumed.behaviour.values == live.behaviour.values
