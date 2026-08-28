"""The bin-packing example: determinism, the lower bound, and the seed's score."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def instances_mod():
    here = Path(__file__).resolve().parents[1] / "examples" / "binpacking"
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    return _load(here / "instances.py", "instances")


@pytest.fixture(scope="module")
def evaluate_mod(instances_mod):
    here = Path(__file__).resolve().parents[1] / "examples" / "binpacking"
    return _load(here / "evaluate.py", "bp_evaluate")


@pytest.fixture(scope="module")
def skeleton_mod(example_dir):
    return _load(example_dir / "skeleton.py", "bp_skeleton")


# -- instances -------------------------------------------------------------


def test_instances_are_deterministic(instances_mod):
    first = instances_mod.build_instances(["proxy"])
    second = instances_mod.build_instances(["proxy"])
    assert [i.items for i in first] == [i.items for i in second]


def test_instance_families_have_the_documented_shapes(instances_mod):
    or_like = instances_mod.or_like("t", 500, 7)
    assert or_like.capacity == 150
    assert all(20 <= s <= 100 for s in or_like.items)

    weib = instances_mod.weibull("t", 500, 7)
    assert weib.capacity == 100
    assert all(1 <= s <= 100 for s in weib.items)
    # Weibull(45, 3) concentrates well below the capacity.
    assert 30 < sum(weib.items) / len(weib.items) < 55


def test_the_holdout_shares_no_instance_with_the_public_sets(instances_mod):
    public = {i.items for i in instances_mod.build_instances(["proxy", "full"])}
    private = {i.items for i in instances_mod.build_instances(["holdout"])}
    assert public.isdisjoint(private)


def test_named_sets_have_the_documented_sizes(instances_mod):
    assert len(instances_mod.build_instances(["proxy"])) == 5
    assert len(instances_mod.build_instances(["full"])) == 20
    assert len(instances_mod.build_instances(["holdout"])) == 5


def test_unknown_input_set_is_reported(instances_mod):
    with pytest.raises(KeyError, match="unknown input set"):
        instances_mod.build_instances(["nope"])


def test_lower_bound_is_the_l1_bound(instances_mod):
    inst = instances_mod.Instance("t", capacity=10, items=(6, 5, 4))
    assert instances_mod.lower_bound(inst) == 2  # ceil(15/10)
    inst = instances_mod.Instance("t", capacity=10, items=(10, 10))
    assert instances_mod.lower_bound(inst) == 2


# -- the skeleton ----------------------------------------------------------


def test_the_seed_rule_is_best_fit(skeleton_mod):
    # Residuals would be 5, 0 and 20 -- best fit takes the exact fit.
    scores = skeleton_mod.priority(30, [35, 30, 50])
    assert scores.index(max(scores)) == 1


def test_pack_never_overfills_a_bin(skeleton_mod, instances_mod):
    inst = instances_mod.or_like("t", 200, 5)
    bins = skeleton_mod.pack(inst.items, inst.capacity)
    assert all(0 <= r < inst.capacity for r in bins)
    assert sum(inst.capacity - r for r in bins) == sum(inst.items)


def test_a_broken_priority_is_counted_not_fatal(skeleton_mod, instances_mod, monkeypatch):
    inst = instances_mod.or_like("t", 100, 5)
    monkeypatch.setattr(skeleton_mod, "priority", lambda item, bins: bins[:-1])
    diag = skeleton_mod.new_diagnostics()
    bins = skeleton_mod.pack(inst.items, inst.capacity, diag)
    assert diag["priority_errors"] > 0  # the incident is a KPI, not an exception
    assert all(r >= 0 for r in bins)  # and the fallback still packs legally


def test_a_raising_priority_is_counted_not_fatal(skeleton_mod, instances_mod, monkeypatch):
    inst = instances_mod.or_like("t", 60, 5)

    def boom(item, bins):
        raise ZeroDivisionError

    monkeypatch.setattr(skeleton_mod, "priority", boom)
    diag = skeleton_mod.new_diagnostics()
    skeleton_mod.pack(inst.items, inst.capacity, diag)
    assert diag["priority_errors"] > 0


def test_a_nan_priority_is_counted(skeleton_mod, instances_mod, monkeypatch):
    inst = instances_mod.or_like("t", 60, 5)
    monkeypatch.setattr(
        skeleton_mod, "priority", lambda item, bins: [float("nan")] * len(bins)
    )
    diag = skeleton_mod.new_diagnostics()
    skeleton_mod.pack(inst.items, inst.capacity, diag)
    assert diag["priority_errors"] > 0


# -- the evaluator ---------------------------------------------------------


def test_the_evaluator_is_deterministic(evaluate_mod, skeleton_mod):
    first = evaluate_mod.evaluate(skeleton_mod, ["proxy"])
    second = evaluate_mod.evaluate(skeleton_mod, ["proxy"])
    assert first["excess_pct"] == second["excess_pct"]
    assert first["bins_used"] == second["bins_used"]


def test_the_best_fit_seed_scores_near_the_published_yardstick(
    evaluate_mod, skeleton_mod
):
    kpis = evaluate_mod.evaluate(skeleton_mod, ["full"])
    # Best fit on OR-Library OR1 is quoted at about 5.8% excess over L1; these
    # instances are OR-shaped, not OR1 itself, so this is a band not an equality.
    assert kpis["excess_pct"] == pytest.approx(5.871, abs=0.001)
    assert 5.0 < kpis["excess_pct"] < 6.5
    assert kpis["priority_errors"] == 0.0
    assert kpis["overfull_bins"] == 0.0
    assert kpis["bins_used"] > kpis["lower_bound"]


def test_the_evaluator_writes_the_kpi_contract(example_dir, tmp_path):
    out = tmp_path / "kpis.json"
    proc = subprocess.run(
        [
            sys.executable,
            str(example_dir / "evaluate.py"),
            "--candidate",
            str(example_dir / "skeleton.py"),
            "--inputs",
            "proxy",
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
        cwd=str(example_dir),
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert "kpis" in payload
    assert isinstance(payload["kpis"]["excess_pct"], float)


def test_a_candidate_missing_priority_is_rejected_by_the_evaluator(
    evaluate_mod, tmp_path
):
    path = tmp_path / "empty.py"
    path.write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(AttributeError, match="does not define"):
        evaluate_mod.load_candidate(path)
