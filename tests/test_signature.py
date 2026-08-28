"""The behaviour signature and the twin filter built on it.

The AST filter catches a child written differently. This one catches a child
that *decides* identically -- a monotone re-expression of best fit, which is
what five of the first real Phase B run's paid children turned out to be.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

from evolvekit.candidate import Candidate
from evolvekit.config import (
    DEFAULT_SIGNATURE_DIGITS,
    DEFAULT_SIGNATURE_IGNORE,
    build_config,
)
from evolvekit.evaluate.cascade import Cascade
from evolvekit.evaluate.signature import (
    BehaviourIndex,
    behaviour_signature,
    round_significant,
)

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


def test_behavioural_auto_skips_a_stochastic_stage(stochastic_stage_config, tmp_path):
    auto_config = _with_behavioural(stochastic_stage_config, "auto")
    cascade = Cascade(auto_config, work_dir=tmp_path / "work")
    cascade.evaluate_generation([candidate("seed", "def rule():\n    return 1")])
    child = candidate("child", "def rule():\n    return 1 * 1")
    result = cascade.evaluate_generation([child])["child"]
    assert result.behaviour_twin_id is None
    assert result.behaviour_signatures == {}


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
