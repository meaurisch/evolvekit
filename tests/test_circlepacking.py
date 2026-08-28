"""The circle-packing example: the referee, the KPIs, and one offline run.

n = 26 in the unit square, the AlphaEvolve / OpenEvolve / ShinkaEvolve
benchmark. Published reference points are quoted in the README as reference
points; nothing here claims to reproduce them. What is asserted here is that
the fixed part cannot be cheated, that every KPI the config names is actually
emitted, and that two offline generations improve on the seed.
"""

from __future__ import annotations

import importlib.util
import json
import math
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from evolvekit.config import load_config
from evolvekit.search.driver import Driver

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_DIR = ROOT / "examples" / "circlepacking"
EXAMPLE_CONFIG = EXAMPLE_DIR / "evolvekit.yaml"

SEED_SUM = 2.5414213562373087
"""5x5 grid of radius-0.1 circles plus one in an interstice at 0.1*(sqrt2 - 1)."""


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def skeleton():
    return _load(EXAMPLE_DIR / "skeleton.py", "circles_skeleton")


def _legal(module, centers, radii, tolerance=1e-9) -> bool:
    for (x, y), r in zip(centers, radii):
        if r < 0 or r > module.wall_clearance(x, y) + tolerance:
            return False
    for i in range(len(radii)):
        for j in range(i + 1, len(radii)):
            if radii[i] <= 0 or radii[j] <= 0:
                continue
            if radii[i] + radii[j] > math.dist(centers[i], centers[j]) + tolerance:
                return False
    return True


# -- the seed --------------------------------------------------------------


def test_the_seed_is_the_documented_grid_plus_one(skeleton):
    centers, radii = skeleton.construct_packing()
    assert len(centers) == len(radii) == skeleton.N_CIRCLES == 26
    assert sorted(radii)[-1] == pytest.approx(0.1)
    assert min(radii) == pytest.approx(0.1 * (math.sqrt(2.0) - 1.0))
    assert skeleton.sum_radii(radii) == pytest.approx(SEED_SUM)


def test_the_seed_is_already_legal(skeleton):
    centers, radii = skeleton.construct_packing()
    assert _legal(skeleton, centers, radii)
    raw = skeleton.violations(centers, radii)
    assert raw == {
        "overlap_violation": 0.0,
        "bounds_violation": 0.0,
        "count_violation": 0.0,
    }, "floating-point noise must not be charged as a violation"


def test_the_seed_survives_the_referee_untouched(skeleton):
    centers, radii = skeleton.construct_packing()
    _fixed_centers, fixed_radii = skeleton.repair(centers, radii)
    assert skeleton.sum_radii(fixed_radii) == pytest.approx(SEED_SUM, abs=1e-12)


def test_the_seed_is_deterministic(skeleton):
    assert skeleton.construct_packing() == skeleton.construct_packing()


# -- the referee -----------------------------------------------------------


def test_repair_never_grows_a_radius(skeleton):
    centers = [(0.5, 0.5), (0.55, 0.5)] + [(0.01 * i, 0.99) for i in range(24)]
    radii = [0.4, 0.4] + [0.001] * 24
    _c, fixed = skeleton.repair(centers, radii)
    assert all(f <= r + 1e-12 for f, r in zip(fixed, radii))


def test_repair_always_returns_a_legal_packing(skeleton):
    centers = [(0.5 + 0.01 * i, 0.5 - 0.01 * i) for i in range(26)]
    radii = [0.5] * 26
    fixed_centers, fixed_radii = skeleton.repair(centers, radii)
    assert _legal(skeleton, fixed_centers, fixed_radii)


def test_repair_pads_a_short_proposal_and_truncates_a_long_one(skeleton):
    short_c, short_r = skeleton.repair([(0.5, 0.5)], [0.4])
    assert len(short_c) == len(short_r) == 26
    assert sum(1 for r in short_r if r > 0) == 1

    long_c, long_r = skeleton.repair([(0.5, 0.5)] * 40, [0.01] * 40)
    assert len(long_c) == len(long_r) == 26


def test_centres_outside_the_square_are_clamped_in(skeleton):
    fixed_centers, _radii = skeleton.repair([(-3.0, 7.0)] * 26, [0.1] * 26)
    assert all(0.0 <= x <= 1.0 and 0.0 <= y <= 1.0 for x, y in fixed_centers)


def test_nonsense_never_raises(skeleton):
    centers = [(float("nan"), 0.5), ("x", 0.5), (0.5, float("inf"))]
    radii = [float("nan"), 0.1, None]
    fixed_centers, fixed_radii = skeleton.repair(centers, radii)
    assert len(fixed_radii) == 26
    assert all(math.isfinite(r) for r in fixed_radii)
    assert _legal(skeleton, fixed_centers, fixed_radii)


# -- the violations --------------------------------------------------------


def test_overlap_is_measured_on_the_proposal_not_the_repair(skeleton):
    centers = [(0.3, 0.5), (0.5, 0.5)] + [(0.0, 0.0)] * 24
    radii = [0.15, 0.15] + [0.0] * 24
    raw = skeleton.violations(centers, radii)
    assert raw["overlap_violation"] == pytest.approx(0.1, abs=1e-6)
    # ... and the repair removes it entirely.
    fixed_centers, fixed_radii = skeleton.repair(centers, radii)
    assert skeleton.violations(fixed_centers, fixed_radii)["overlap_violation"] == 0.0


def test_a_circle_hanging_out_of_the_square_is_a_bounds_violation(skeleton):
    raw = skeleton.violations([(0.5, 0.5)] * 26, [0.6] * 26)
    assert raw["bounds_violation"] > 0.0


def test_a_centre_outside_the_square_is_a_bounds_violation(skeleton):
    raw = skeleton.violations([(1.5, 0.5)] + [(0.0, 0.0)] * 25, [0.0] * 26)
    assert raw["bounds_violation"] == pytest.approx(0.5, abs=1e-6)


def test_the_wrong_number_of_circles_is_a_count_violation(skeleton):
    assert skeleton.violations([(0.5, 0.5)] * 20, [0.05] * 20)["count_violation"] == 6.0
    assert skeleton.violations([(0.5, 0.5)] * 30, [0.01] * 30)["count_violation"] == 4.0


def test_violations_are_continuous_in_the_size_of_the_mistake(skeleton):
    def overlap(gap):
        centers = [(0.5 - gap / 2, 0.5), (0.5 + gap / 2, 0.5)] + [(0.0, 0.0)] * 24
        return skeleton.violations(centers, [0.1, 0.1] + [0.0] * 24)[
            "overlap_violation"
        ]

    assert overlap(0.30) == 0.0
    assert 0.0 < overlap(0.19) < overlap(0.15) < overlap(0.10)


# -- the descriptors -------------------------------------------------------


def test_equal_radii_have_no_variance(skeleton):
    behaviour = skeleton.descriptors([(0.5, 0.5)] * 26, [0.05] * 26)
    assert behaviour["radius_variance"] == pytest.approx(0.0)


def test_the_seed_touches_the_boundary_but_not_everywhere(skeleton):
    centers, radii = skeleton.construct_packing()
    behaviour = skeleton.descriptors(centers, radii)
    assert 0.0 < behaviour["boundary_fraction"] < 1.0
    assert behaviour["radius_variance"] > 0.0


def test_the_descriptors_are_not_monotone_in_the_objective(skeleton):
    """Two packings with the same sum and different variance."""
    equal = skeleton.descriptors([(0.0, 0.0)] * 26, [0.1] * 26)
    mixed = skeleton.descriptors([(0.0, 0.0)] * 26, [0.15] * 13 + [0.05] * 13)
    assert equal["radius_variance"] < mixed["radius_variance"]


# -- the evaluator ---------------------------------------------------------


def _run_evaluator(tmp_path: Path, source: Path) -> dict:
    out = tmp_path / "kpis.json"
    result = subprocess.run(
        [
            sys.executable,
            "evaluate.py",
            "--candidate",
            str(source),
            "--inputs",
            "full",
            "--out",
            str(out),
        ],
        cwd=str(EXAMPLE_DIR),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(out.read_text(encoding="utf-8"))["kpis"]


def test_the_evaluator_emits_every_kpi_the_config_names(tmp_path):
    kpis = _run_evaluator(tmp_path, EXAMPLE_DIR / "skeleton.py")
    config = load_config(EXAMPLE_CONFIG)
    named = {config.evaluate.score.objective}
    named.update(config.evaluate.score.weights)
    named.update(p.kpi for p in config.evaluate.penalties)
    named.update(d.kpi for d in config.search.archive.descriptors)
    named.discard("complexity")  # emitted by the static stage
    assert named <= set(kpis), f"missing {sorted(named - set(kpis))}"
    assert kpis["sum_radii"] == pytest.approx(SEED_SUM)
    assert kpis["circles"] == 26.0


def test_the_evaluator_is_deterministic(tmp_path):
    first = _run_evaluator(tmp_path / "a", EXAMPLE_DIR / "skeleton.py")
    second = _run_evaluator(tmp_path / "b", EXAMPLE_DIR / "skeleton.py")
    assert first["sum_radii"] == second["sum_radii"]
    assert first["radii_sorted"] == second["radii_sorted"]


def test_a_crashing_construction_is_counted_not_fatal(tmp_path):
    source = tmp_path / "broken.py"
    text = (EXAMPLE_DIR / "skeleton.py").read_text(encoding="utf-8")
    marker = "# EVOLVE-BLOCK-START"
    head = text.split(marker)[0]
    source.write_text(
        head
        + marker
        + "\ndef construct_packing():\n    raise ValueError('nope')\n"
        + "# EVOLVE-BLOCK-END\n",
        encoding="utf-8",
    )
    kpis = _run_evaluator(tmp_path, source)
    assert kpis["construction_errors"] == 1.0
    assert kpis["sum_radii"] == 0.0
    assert kpis["count_violation"] == 26.0


def test_the_stage_command_rejects_an_unknown_input_set():
    result = subprocess.run(
        [
            sys.executable,
            "evaluate.py",
            "--candidate",
            "skeleton.py",
            "--inputs",
            "proxy",
            "--out",
            "unused.json",
        ],
        cwd=str(EXAMPLE_DIR),
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "unknown input set" in result.stderr


# -- the config ------------------------------------------------------------


def test_the_example_has_two_stages_and_no_holdout():
    config = load_config(EXAMPLE_CONFIG)
    assert [s.id for s in config.evaluate.stages] == ["static", "full"]
    assert config.final_stage.private_inputs == ()
    assert config.evaluate.score.direction == "maximize"


def test_the_real_provider_variant_inherits_the_shared_sections():
    base = load_config(EXAMPLE_CONFIG)
    variant = load_config(EXAMPLE_DIR / "evolvekit.claude.yaml")
    assert variant.problem == base.problem
    assert variant.evaluate == base.evaluate
    assert variant.models.small.provider == "claude-cli"
    assert variant.budget.max_usd == 5.0
    assert variant.stop.max_usd_since_improvement == 0.50


# -- two generations, offline ---------------------------------------------


@pytest.fixture(scope="module")
def offline_run(tmp_path_factory):
    config = load_config(EXAMPLE_CONFIG)
    config = replace(
        config,
        search=replace(
            config.search,
            generations=2,
            children_per_generation=2,
            big_step_every=99,
            scratchpad_every=0,
            inspirations=0,
            adaptive_children=None,
            operators={"rewrite": 1.0},
        ),
    )
    driver = Driver(config, run_dir=tmp_path_factory.mktemp("circles"))
    return driver, driver.run()


def test_two_offline_generations_improve_on_the_seed(offline_run):
    _driver, summary = offline_run
    assert summary.seed_score == pytest.approx(SEED_SUM, abs=1e-6)
    assert summary.best is not None
    # The scripted improvement is a penalty relaxation from the grid: it reaches
    # about 2.579 raw, and about 2.570 once its residual overlap is charged.
    assert summary.best.score > summary.seed_score
    assert summary.best.score == pytest.approx(2.570, abs=0.02)
    assert summary.improvement > 0.02


def test_the_run_records_the_packing_kpis(offline_run):
    driver, summary = offline_run
    best = summary.best
    assert best.kpis["circles"] == 26.0
    assert best.kpis["sum_radii"] > SEED_SUM
    assert "radius_variance" in best.kpis
    assert best.cell is not None and len(best.cell) == 2


def test_an_illegal_proposal_is_penalised_and_never_voided(offline_run):
    driver, _summary = offline_run
    penalised = [
        row
        for row in driver.ledger.runs()
        if row["penalty_total"] > 0 and not row["rejected"]
    ]
    assert penalised, "no candidate exercised the penalty path"
    for row in penalised:
        assert row["score"] is not None
        assert row["score"] > driver.config.evaluate.failure_score
        assert row["score"] < row["kpis"]["sum_radii"]


def test_every_candidate_is_scored_on_a_legal_packing(offline_run):
    """The referee is the anti-gaming boundary: no score without a repair."""
    driver, _summary = offline_run
    for row in driver.ledger.runs():
        if row["rejected"] or not row["kpis"]:
            continue
        assert row["kpis"]["sum_radii"] <= 26 * 0.5
        assert row["kpis"]["circles"] <= 26
