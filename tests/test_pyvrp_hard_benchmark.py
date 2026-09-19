"""The `pyvrp_hard` tuning benchmark: generator, loader, solver front end.

Every test here needs PyVRP, which the core suite deliberately does not depend
on -- `python tasks.py check` in the base interpreter skips this whole file.
Run it with the benchmark's interpreter instead:

    .venv-pyvrp/Scripts/python -m pytest tests/test_pyvrp_hard_benchmark.py -q

What is asserted: that the generator is deterministic to the byte and that the
committed smoke instances and `manifest.json` are exactly what it produces;
that EVERY modelling feature of PyVRP 0.14.0 occurs in EVERY instance (the
smoke set always, the large sets whenever `instances/` has been generated);
that the scenario table really is diverse; that the vectorised travel matrices
agree with the generator's scalar travel model; that `solve.py` defaults are
PyVRP's defaults; and that `solve.py` behaves like a foreign-language solver:
flags in, one JSON line out, exit code 2 and one line on stderr for nonsense.
That the defaults find a feasible solution on the large instances is NOT
asserted here -- it takes half an hour; `verify.py` does it and
`verification.json` records it.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import json
import random
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("pyvrp", reason="the pyvrp_hard benchmark needs the pyvrp extra")

MIN_PYVRP = (0, 14)
"""`Location`, `Shipment`, reload depots, overtime, `PerturbationParams` and
the ILS callbacks the solver front end uses all arrived with 0.14: an older
PyVRP is a skip, not a failure."""

_installed = importlib.metadata.version("pyvrp")
if tuple(int(part) for part in _installed.split(".")[:2]) < MIN_PYVRP:
    pytest.skip(
        f"the pyvrp_hard benchmark needs pyvrp >= {'.'.join(map(str, MIN_PYVRP))}; "
        f"this interpreter has {_installed}",
        allow_module_level=True,
    )

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmarks" / "pyvrp_hard"
SMOKE_DIR = BENCH / "smoke"
INSTANCES_DIR = BENCH / "instances"
SOLVE = BENCH / "solve.py"

RESULT_KEYS = {
    "objective", "feasible", "cost", "penalised_cost", "iterations", "runtime_s",
    "load_s", "time_limit_s", "num_routes", "num_trips", "distance", "duration",
    "overtime", "fixed_cost", "distance_cost", "duration_cost", "prizes_collected",
    "prizes_uncollected", "unserved_optional_clients", "unserved_optional_shipments",
    "vehicle_types_used", "convergence", "instance", "seed", "params", "pyvrp_version",
}


def _load(name: str):
    """Path-based import under the key the benchmark's own modules use for
    each other, so a test and `generate.py` share one `instance` module."""
    key = f"pyvrp_hard_{name}"
    if key in sys.modules:
        return sys.modules[key]
    spec = importlib.util.spec_from_file_location(key, BENCH / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def generator():
    return _load("generate")


@pytest.fixture(scope="module")
def loader():
    return _load("instance")


@pytest.fixture(scope="module")
def solver():
    return _load("solve")


@pytest.fixture(scope="module")
def manifest():
    return json.loads((BENCH / "manifest.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def smoke_texts(generator):
    return generator.generate_set("smoke")


@pytest.fixture(scope="module")
def smoke_instances(loader):
    return [loader.load_instance(path) for path in sorted(SMOKE_DIR.glob("s*.json"))]


# -- the generator ---------------------------------------------------------


def test_generation_is_byte_identical(generator, smoke_texts):
    assert generator.generate_set("smoke") == smoke_texts
    for _, text in smoke_texts:
        assert "\r" not in text and text.endswith("}\n")
        text.encode("ascii")  # ASCII only, or this raises


def test_the_fresh_scenarios_are_drawn_deterministically(generator):
    assert generator.fresh_scenarios() == generator.fresh_scenarios()


def test_the_committed_smoke_set_is_what_the_generator_writes(smoke_texts, manifest):
    assert len(smoke_texts) == 2
    for file_name, text in smoke_texts:
        on_disk = (SMOKE_DIR / file_name).read_bytes()
        assert on_disk == text.encode("ascii"), file_name
        entry = manifest["instances"][file_name[: -len(".json")]]
        assert entry["set"] == "smoke"
        assert entry["bytes"] == len(on_disk)
        assert entry["sha256"] == hashlib.sha256(on_disk).hexdigest()


def test_all_stored_numbers_are_integers(smoke_instances):
    def walk(value):
        if isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        else:
            assert not isinstance(value, float), value

    for instance in smoke_instances:
        walk(instance)


def test_a_tuning_instance_regenerates_to_its_manifest_hash(generator, manifest):
    scenario = generator.TUNING_SCENARIOS[0]
    text = generator.dumps(generator.generate(scenario, "tuning"))
    entry = manifest["instances"][scenario.name]
    assert entry["sha256"] == hashlib.sha256(text.encode("ascii")).hexdigest()
    assert entry["clients"] == scenario.clients == 1000


@pytest.mark.slow
def test_every_instance_regenerates_to_its_manifest_hash(generator, manifest):
    seen = set()
    for which in ("tuning", "fresh", "smoke"):
        for file_name, text in generator.generate_set(which):
            entry = manifest["instances"][file_name[: -len(".json")]]
            assert entry["set"] == which
            assert entry["sha256"] == hashlib.sha256(text.encode("ascii")).hexdigest()
            seen.add(file_name[: -len(".json")])
    assert seen == set(manifest["instances"])


def test_a_scenario_that_cannot_work_is_refused(generator):
    from dataclasses import replace

    row = generator.SMOKE_SCENARIOS[0]
    with pytest.raises(generator.GenerationError, match="van class is mandatory"):
        generator.generate(replace(row, classes=("box_truck", "heavy_truck", "evening_van")))
    with pytest.raises(generator.GenerationError, match="third profile"):
        generator.generate(
            replace(generator.TUNING_SCENARIOS[-1], profiles=("light", "heavy", "bike"))
        )


# -- every feature, every instance -----------------------------------------


def test_every_feature_is_used_by_every_smoke_instance(loader, smoke_instances, manifest):
    assert len(smoke_instances) == 2
    for instance in smoke_instances:
        assert loader.missing_features(instance) == [], instance["name"]
        inventory = loader.feature_inventory(instance)
        assert inventory == manifest["instances"][instance["name"]]["features"]
        assert all(isinstance(v, int) for v in inventory.values())
    sizes = sorted(len(instance["clients"]) for instance in smoke_instances)
    assert sizes == [60, 120]


def test_every_feature_is_used_by_every_instance_in_the_manifest(loader, manifest):
    assert len(manifest["instances"]) == 16
    for name, entry in manifest["instances"].items():
        for key, least in loader.FEATURE_MINIMUMS.items():
            assert entry["features"][key] >= least, (name, key)
        features = entry["features"]
        assert features["load_dimensions_in_use"] == features["load_dimensions"], name
        assert features["clients"] == entry["clients"] == entry["scenario"]["clients"]
        # Shipments are 0.5-2 % of the client count on the large sets.
        if entry["set"] != "smoke":
            assert 0.005 <= entry["shipments"] / entry["clients"] <= 0.02, name
            assert 0.05 <= features["clients_on_shared_locations"] / entry["clients"] <= 0.45


def test_every_feature_is_used_by_every_generated_instance(loader, manifest):
    paths = sorted(INSTANCES_DIR.glob("*.json")) if INSTANCES_DIR.is_dir() else []
    paths = [p for p in paths if p.name not in ("verification.json", "manifest.json")]
    if not paths:
        pytest.skip("instances/ has not been generated (it is gitignored)")
    for path in paths:
        instance = loader.load_instance(path)
        assert loader.missing_features(instance) == [], path.name
        entry = manifest["instances"][instance["name"]]
        assert entry["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest(), path.name


def test_groups_shipments_and_prizes_are_shaped_as_documented(smoke_instances):
    for instance in smoke_instances:
        clients = instance["clients"]
        for group in instance["groups"]:
            members = [clients[i] for i in group["clients"]]
            assert 2 <= len(members) <= 3
            # PyVRP refuses a required client inside a mutually exclusive group.
            assert all(not m["required"] for m in members)
            assert all((m["prize"] > 0) == (not group["required"]) for m in members)
            if group["kind"] == "alt_window":
                assert len({m["location"] for m in members}) == 1
                assert len({(m["tw_early"], m["tw_late"]) for m in members}) == len(members)
        for c in clients:
            if c["group"] is None and not c["required"]:
                assert c["prize"] > 0
        for s in instance["shipments"]:
            assert (s["prize"] > 0) == (not s["required"])
            assert s["pickup_location"] != s["delivery_location"]


# -- the scenario table ----------------------------------------------------


def test_the_tuning_scenarios_are_structurally_diverse(generator):
    rows = generator.TUNING_SCENARIOS
    sizes = [row.clients for row in rows]
    assert len(rows) == 10
    assert min(sizes) == 1000 and max(sizes) == 3000 and len(set(sizes)) == 10
    assert len({row.geography for row in rows}) >= 4
    assert {"tight", "loose"} <= {row.tw_regime for row in rows}
    assert len({row.tw_regime for row in rows}) == 4
    assert {row.depots for row in rows} == {2, 3, 4, 5}
    assert {len(row.classes) for row in rows} >= {3, 4, 5}
    assert {row.load_dims for row in rows} == {2, 3}
    assert {len(row.profiles) for row in rows} == {2, 3}
    assert min(row.area_km for row in rows) <= 25 and max(row.area_km for row in rows) >= 150
    assert {row.reload_policy for row in rows} == set(generator.RELOAD_POLICIES)
    assert {row.overtime_policy for row in rows} == set(generator.OVERTIME_POLICIES)
    assert {row.service for row in rows} == set(generator.SERVICE_KINDS)
    assert len({row.seed for row in rows}) == 10
    for row in rows:
        assert len(row.profiles) == 2 or row.clients <= 1800
        assert 5 <= row.shipment_permille <= 20
        assert 5 <= row.shared_location_pct <= 20


def test_the_fresh_scenarios_are_new_draws_from_the_same_families(generator):
    fresh = generator.fresh_scenarios()
    tuning = generator.TUNING_SCENARIOS
    assert [row.clients for row in fresh] == [1100, 1700, 2300, 2900]
    assert not {row.seed for row in fresh} & {row.seed for row in tuning}
    assert not {row.name for row in fresh} & {row.name for row in tuning}
    assert len({row.geography for row in fresh}) == 4
    assert len({row.tw_regime for row in fresh}) == 4
    for row in fresh:
        assert row.geography in generator.GEOGRAPHIES
        assert 2 <= row.depots <= 5 and 3 <= len(row.classes) <= 5


# -- loading ---------------------------------------------------------------


def test_build_problem_data_and_the_travel_model_agree(generator, loader, smoke_instances):
    import numpy as np

    for instance in smoke_instances:
        data = loader.build_problem_data(instance)
        assert data.num_clients == len(instance["clients"])
        assert data.num_locations == len(instance["locations"])
        assert data.num_depots == len(instance["depots"])
        assert data.num_groups == len(instance["groups"])
        assert data.num_shipments == len(instance["shipments"])
        assert data.num_profiles == len(instance["profiles"])
        assert data.num_load_dimensions == len(instance["vehicle_types"][0]["capacity"])
        assert data.num_vehicles == sum(
            vt["num_available"] for vt in instance["vehicle_types"]
        )

        travel = generator._Travel(instance)
        rng = random.Random(5)
        n = data.num_locations
        restricted = instance["restricted_locations"]
        for p, profile in enumerate(instance["profiles"]):
            dist, time = data.distance_matrix(p), data.duration_matrix(p)
            assert dist.dtype == np.int64 and not np.diagonal(dist).any()
            assert (dist != dist.T).any(), "every profile is asymmetric"
            for _ in range(500):
                i, j = rng.randrange(n), rng.randrange(n)
                assert (int(dist[i, j]), int(time[i, j])) == travel(i, j, p)
            row = dist[restricted[0]]
            if profile["restricted"]:
                assert (np.delete(row, restricted[0]) == generator.FORBIDDEN).all()
            else:
                assert row.max() < generator.FORBIDDEN
        # PyVRP's own opinion: every stop can be served by SOME vehicle alone.
        assert loader.unservable_stops(data) == []


def test_the_infeasibility_price_is_above_any_feasible_cost(loader, smoke_instances):
    for instance in smoke_instances:
        pricing = loader.infeasibility_pricing(instance)
        dearest_fleet = sum(
            vt["num_available"] * vt["fixed_cost"] for vt in instance["vehicle_types"]
        )
        assert pricing["offset"] > dearest_fleet
        assert len(pricing["load_penalties"]) == len(instance["vehicle_types"][0]["capacity"])
        assert min(pricing["load_penalties"]) >= 1 and pricing["tw_penalty"] >= 1


# -- the solver front end --------------------------------------------------


def test_the_defaults_are_pyvrps_defaults(solver):
    import pyvrp
    import pyvrp.search

    defaults = solver.resolve_parameters({}, None)
    theirs = {}
    for params in (
        pyvrp.IteratedLocalSearchParams(),
        pyvrp.PenaltyParams(),
        pyvrp.search.NeighbourhoodParams(),
        pyvrp.search.PerturbationParams(),
    ):
        for name in defaults:
            if hasattr(params, name):
                theirs[name] = getattr(params, name)
    assert len(theirs) == 15
    assert {name: defaults[name] for name in theirs} == theirs
    assert solver.resolve_operators(defaults) == [
        op.__name__ for op in pyvrp.search.OPERATORS
    ]
    everything = {name: True for name, kind, _, _ in solver.PARAMETERS if kind is bool}
    assert len(solver.resolve_operators({**defaults, **everything})) == 23
    nothing = {name: False for name in everything}
    always_on = solver.resolve_operators({**defaults, **nothing})
    assert len(always_on) == 11 and "Relocate1" in always_on and "ReplaceGroup" in always_on


def test_flags_beat_the_json_and_the_json_beats_the_defaults(solver):
    resolved = solver.resolve_parameters(
        {"num_neighbours": "30", "use_swap33": "TRUE"},
        '{"num_neighbours": 40, "history-length": 200.0, "use_swap11": false}',
    )
    assert resolved["num_neighbours"] == 30
    assert resolved["history_length"] == 200 and isinstance(resolved["history_length"], int)
    assert resolved["use_swap33"] is True and resolved["use_swap11"] is False
    assert resolved["penalty_increase"] == 1.5


@pytest.mark.parametrize(
    "flags, params_json, needle",
    [
        ({"min_perturbations": "30"}, None, "--max-perturbations"),
        ({"penalty_increase": "0.9"}, None, "--penalty-increase"),
        ({"penalty_decrease": "1.5"}, None, "--penalty-decrease"),
        ({"target_feasible": "-0.1"}, None, "--target-feasible"),
        ({"history_length": "0"}, None, "--history-length"),
        ({"num_neighbours": "0"}, None, "--num-neighbours"),
        ({"num_neighbours": "12.5"}, None, "whole number"),
        ({"min_penalty": "10", "max_penalty": "1"}, None, "--min-penalty"),
        ({"max_penalty": "1e12"}, None, "overflow"),
        ({"weight_wait_time": "-1"}, None, "--weight-wait-time"),
        ({"exhaustive_on_best": "perhaps"}, None, "true or false"),
        ({"max_penalty": "inf"}, None, "finite"),
        ({}, '{"no_such_parameter": 1}', "unknown parameter"),
        ({}, '{"num_neighbours": true}', "must be a number"),
        ({}, "{broken", "not valid JSON"),
        ({}, "[1, 2]", "neither a JSON object nor an existing file"),
    ],
)
def test_invalid_parameters_are_named(solver, flags, params_json, needle):
    with pytest.raises(solver.ParameterError) as caught:
        solver.resolve_parameters(flags, params_json)
    assert needle in str(caught.value)
    assert "\n" not in str(caught.value)


def _run_solver(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SOLVE), *args], capture_output=True, text=True, check=False
    )


@pytest.mark.parametrize(
    "extra",
    [
        ["--min-perturbations", "30"],
        ["--penalty-increase", "0.5"],
        ["--symmetric-proximity", "sometimes"],
        ["--params-json", '{"bogus": 1}'],
        ["--no-such-flag", "1"],
    ],
)
def test_the_command_line_rejects_nonsense_with_exit_code_two(extra):
    done = _run_solver(
        "--instance", str(SMOKE_DIR / "s01-n60-uniform-mixed.json"),
        "--time-limit", "1", "--seed", "1", *extra,
    )
    assert done.returncode == 2
    assert done.stdout == ""
    assert "Traceback" not in done.stderr
    assert len(done.stderr.strip().splitlines()) == 1
    assert done.stderr.startswith("solve.py: error: ")


def test_the_command_line_rejects_a_missing_instance_and_a_bad_limit():
    for args in (
        ["--instance", "no-such-file.json", "--time-limit", "1", "--seed", "1"],
        ["--instance", str(SOLVE), "--time-limit", "1", "--seed", "1"],
        ["--instance", str(SMOKE_DIR / "s01-n60-uniform-mixed.json"),
         "--time-limit", "0", "--seed", "1"],
        ["--instance", str(SMOKE_DIR / "s01-n60-uniform-mixed.json"),
         "--time-limit", "1", "--seed", "-1"],
    ):
        done = _run_solver(*args)
        assert done.returncode == 2, args
        assert "Traceback" not in done.stderr
        assert len(done.stderr.strip().splitlines()) == 1


@pytest.mark.slow
def test_the_solver_returns_one_json_line_and_honours_the_flags(tmp_path, loader):
    out = tmp_path / "result.json"
    instance_path = SMOKE_DIR / "s01-n60-uniform-mixed.json"
    done = _run_solver(
        "--instance", str(instance_path), "--time-limit", "3", "--seed", "7",
        "--out", str(out), "--num-neighbours", "25", "--use-swap33", "true",
        "--use-swap-tails", "false", "--exhaustive-on-best", "false",
        "--params-json", '{"history_length": 120, "num_neighbours": 40, "max_penalty": 50000}',
    )
    assert done.returncode == 0, done.stderr
    assert "Traceback" not in done.stderr
    last = done.stdout.strip().splitlines()[-1]
    result = json.loads(last)
    assert json.loads(out.read_text(encoding="utf-8")) == result
    assert RESULT_KEYS <= set(result)

    assert result["feasible"] is True
    assert result["instance"] == "s01-n60-uniform-mixed"
    assert result["seed"] == 7 and result["time_limit_s"] == 3.0
    assert result["pyvrp_version"] == _installed
    assert result["objective"] == result["cost"] == result["penalised_cost"] > 0
    assert result["iterations"] > 0
    # The limit bounds the whole solve call; one iteration of overrun at most.
    assert 3.0 <= result["runtime_s"] < 6.0 and result["load_s"] >= 0

    params = result["params"]
    assert params["num_neighbours"] == 25  # the flag beat the JSON
    assert params["history_length"] == 120 and params["max_penalty"] == 50000
    assert params["use_swap33"] is True and params["use_swap_tails"] is False
    assert params["exhaustive_on_best"] is False
    assert params["penalty_increase"] == 1.5  # untouched default
    assert "Swap33" in result["operators"] and "SwapTails" not in result["operators"]

    convergence = result["convergence"]
    assert len(convergence) == 10 and convergence[-1] == result["objective"]
    known = [value for value in convergence if value is not None]
    assert known == sorted(known, reverse=True)

    # The cost decomposes as PyVRP documents it.
    assert result["cost"] == (
        result["fixed_cost"] + result["distance_cost"] + result["duration_cost"]
        + result["prizes_uncollected"]
    )
    assert result["num_trips"] >= result["num_routes"] >= 1
    pricing = loader.infeasibility_pricing(loader.load_instance(instance_path))
    assert result["objective"] < pricing["offset"]
