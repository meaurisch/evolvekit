"""`param_lhs`: the zero-token operator, against a skeleton that declares params.

The bin-packing example deliberately declares none -- its evolve block is a
scoring rule, not a set of constants -- so the operator is exercised here
against a synthetic skeleton instead of by putting a fake `# PARAMS:` line in
the demo.
"""

from __future__ import annotations

import textwrap
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from evolvekit.candidate import Candidate
from evolvekit.config import load_config
from evolvekit.providers.fake import FakeProvider
from evolvekit.search.driver import Driver
from evolvekit.search.operators import param_lhs
from evolvekit.search.params import (
    apply_params,
    current_values,
    declared_ranges,
    has_params,
    param_variant,
)

BLOCK = '''\
# PARAMS: {"ALPHA": [0.0, 10.0], "STEPS": [1, 9]}
ALPHA = 5.0
STEPS = 3


def value():
    """Peaks at ALPHA = 8 and STEPS = 7."""
    return -abs(ALPHA - 8.0) - abs(STEPS - 7)
'''


def candidate(block: str = BLOCK) -> Candidate:
    return Candidate(
        id="p", generation=1, block=block, source="", operator="rewrite", score=-1.0
    )


# -- the convention --------------------------------------------------------


def test_declared_ranges_are_read_from_the_params_line():
    assert declared_ranges(BLOCK) == {"ALPHA": [0.0, 10.0], "STEPS": [1.0, 9.0]}
    assert has_params(BLOCK)


def test_a_block_without_the_marker_declares_nothing():
    assert declared_ranges("def f():\n    return 1\n") == {}
    assert not has_params("def f():\n    return 1\n")


@pytest.mark.parametrize(
    "line",
    [
        '# PARAMS: {"A": [1.0]}',  # not a pair
        '# PARAMS: {"A": [2.0, 1.0]}',  # not ordered
        '# PARAMS: {"A": "wide"}',  # not a range
        "# PARAMS: not a dict at all",
        '# PARAMS: {"not an identifier": [0, 1]}',
    ],
)
def test_a_malformed_declaration_is_ignored_rather_than_fatal(line):
    assert declared_ranges(f"{line}\nA = 1\n") == {}


def test_current_values_reads_module_level_assignments_only():
    values = current_values(BLOCK, {"ALPHA", "STEPS"})
    assert values == {"ALPHA": 5.0, "STEPS": 3}
    nested = "def f():\n    ALPHA = 1.0\n    return ALPHA\n"
    assert current_values(nested, {"ALPHA"}) == {}


def test_apply_params_rewrites_only_the_assignment_lines():
    updated = apply_params(BLOCK, {"ALPHA": 8.25, "STEPS": 7})
    assert "ALPHA = 8.25" in updated and "STEPS = 7" in updated
    assert '# PARAMS: {"ALPHA": [0.0, 10.0], "STEPS": [1, 9]}' in updated
    assert 'return -abs(ALPHA - 8.0) - abs(STEPS - 7)' in updated
    assert current_values(updated, {"ALPHA", "STEPS"}) == {"ALPHA": 8.25, "STEPS": 7}


def test_a_variant_is_inside_the_declared_ranges_and_keeps_integer_types():
    block, values = param_variant(BLOCK, seed=3)
    assert 0.0 <= values["ALPHA"] <= 10.0
    assert 1 <= values["STEPS"] <= 9 and isinstance(values["STEPS"], int)
    assert values != {"ALPHA": 5.0, "STEPS": 3}, "variant 0 is the base point"
    assert current_values(block, {"ALPHA", "STEPS"}) == values


def test_variants_are_reproducible_from_the_seed():
    assert param_variant(BLOCK, seed=11) == param_variant(BLOCK, seed=11)
    assert param_variant(BLOCK, seed=11) != param_variant(BLOCK, seed=12)


def test_a_block_with_nothing_to_sweep_yields_no_variant():
    assert param_variant("def f():\n    return 1\n", seed=1) is None
    # Declared but never assigned: still nothing to rewrite.
    assert param_variant('# PARAMS: {"A": [0, 1]}\n\n\ndef f():\n    return 1\n', seed=1) is None


# -- the operator ----------------------------------------------------------


def test_the_operator_costs_no_tokens_and_reports_what_it_changed():
    outcome = param_lhs(candidate(), seed=5)
    assert outcome.ok and outcome.mode == "param_lhs"
    assert outcome.completion is None and outcome.messages == []
    assert set(outcome.meta["params"]) == {"ALPHA", "STEPS"}
    assert outcome.block != BLOCK


def test_the_operator_fails_loudly_on_a_param_less_block():
    outcome = param_lhs(candidate("def f():\n    return 1\n"), seed=1)
    assert not outcome.ok
    assert "no sweepable parameters" in outcome.error


# -- inside the loop -------------------------------------------------------


@pytest.fixture
def sweepable_project(tmp_path: Path) -> Path:
    """A tiny problem whose whole search space is two constants."""
    (tmp_path / "skeleton.py").write_text(
        '"""Synthetic skeleton whose evolve block is nothing but constants."""\n\n'
        "from __future__ import annotations\n\n"
        '__all__ = ["value"]\n\n\n'
        "# EVOLVE-BLOCK-START\n" + BLOCK + "# EVOLVE-BLOCK-END\n",
        encoding="utf-8",
        newline="\n",
    )
    (tmp_path / "evaluate.py").write_text(
        textwrap.dedent(
            """\
            import argparse, importlib.util, json
            from pathlib import Path

            parser = argparse.ArgumentParser()
            parser.add_argument("--candidate", required=True)
            parser.add_argument("--inputs", required=True)
            parser.add_argument("--out", required=True)
            args = parser.parse_args()

            spec = importlib.util.spec_from_file_location("cand", args.candidate)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(
                json.dumps({"kpis": {"value": float(module.value())}}),
                encoding="utf-8",
            )
            """
        ),
        encoding="utf-8",
        newline="\n",
    )
    config = {
        "problem": {"skeleton": "skeleton.py", "required_functions": ["value"]},
        "evaluate": {
            "stages": [
                {"id": "static", "kind": "builtin-static", "timeout": 30},
                {
                    "id": "full",
                    "kind": "command",
                    "command": (
                        "python evaluate.py --candidate {candidate} "
                        "--inputs {inputs} --out {out}"
                    ),
                    "inputs": ["all"],
                    "timeout": 60,
                },
            ],
            "score": {"objective": "value", "direction": "maximize"},
        },
        "models": {
            "small": {"provider": "fake", "model": "s", "options": {"responses": ["x"]}},
            "strong": {"provider": "fake", "model": "b", "options": {"responses": ["x"]}},
        },
        "search": {
            "children_per_generation": 3,
            "generations": 3,
            "big_step_every": 99,
            "scratchpad_every": 0,
            "seed": 4,
            "operators": {"param_lhs": 1.0},
            "archive": {"descriptors": [{"kpi": "value", "bins": 4, "range": "auto"}]},
        },
        "stop": {"patience": 9, "epsilon": 0.0001},
    }
    (tmp_path / "evolvekit.yaml").write_text(
        yaml.safe_dump(config, sort_keys=False), encoding="utf-8", newline="\n"
    )
    return tmp_path / "evolvekit.yaml"


@pytest.mark.slow
def test_a_whole_run_of_param_lhs_spends_nothing_and_still_improves(
    sweepable_project, tmp_path
):
    config = load_config(sweepable_project)
    provider = FakeProvider(["never called"])
    driver = Driver(
        config,
        run_dir=tmp_path / "run",
        providers={"small": provider, "strong": provider},
    )
    summary = driver.run()

    assert provider.calls == [], "param_lhs must not call a model"
    assert summary.totals.get("usd", 0.0) == 0.0
    assert summary.totals.get("calls", 0.0) == 0.0
    assert summary.best is not None and summary.best.score > summary.seed_score
    operators = {c.operator for c in driver.archive if c.parent_id}
    assert operators == {"param_lhs"}
    for child in driver.archive[1:]:
        assert child.model == "none" and child.provider == "none"
        assert current_values(child.block, {"ALPHA", "STEPS"}) != {
            "ALPHA": 5.0,
            "STEPS": 3,
        }


def test_param_lhs_is_not_scheduled_against_a_param_less_skeleton(tmp_path):
    from tests.conftest import EXAMPLE_CONFIG

    config = load_config(EXAMPLE_CONFIG)
    config = replace(
        config,
        search=replace(config.search, operators={"param_lhs": 1.0, "diff": 1.0}),
    )
    driver = Driver(config, run_dir=tmp_path / "noparams")
    plan = driver._plan_operators(1, plateau=False)
    assert "param_lhs" not in plan, "the bin-packing block declares no parameters"
    assert set(plan) == {"diff"}
