"""Regenerate the PyVRP harness's samples: ten small delivery requests (60 to
240 tasks) in `pyvrp-request/1`, with tags.

    python harnesses/pyvrp/samples/make_samples.py

The requests come from the pyvrp_hard benchmark's generator
(benchmarks/pyvrp_hard/generate.py, so this runs inside the evolvekit
repository), one scenario row each, and every modelling feature of PyVRP is
in every one of them. A seeded pass then adds the tags a study can select by:

* tasks: `vip`, `fragile` and `business` on a share of the clients, `frozen`
  on clients that receive cold boxes (or, without a cold-box dimension, on a
  share of them), `express` on a share of the shipments;
* vehicle types: `electric` (cargo bikes, evening vans, some vans), `rented`
  (some box and heavy trucks), `cooled` (types that carry cold boxes).

Everything is seeded, and the files are written canonically (the generator's
`dumps`), so a rerun writes the same bytes.
"""

from __future__ import annotations

import importlib.util
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
GENERATOR = HERE.parents[2] / "benchmarks" / "pyvrp_hard" / "generate.py"
TAG_SEED = 20_260_926


def _generator():
    if not GENERATOR.is_file():
        sys.exit(f"the benchmark generator is not at {GENERATOR}: run this inside the evolvekit repository")
    spec = importlib.util.spec_from_file_location("pyvrp_hard_generate", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def scenarios(g):
    """Ten rows of the generator's scenario table, small and varied. Every one
    has vans, box trucks and evening vans (the route-costs template varies
    those three classes)."""
    base = ("van", "box_truck", "evening_van")
    s = g._s
    return (
        s("city-60", 9101, 56, geography="uniform", area_km=18, tw_regime="mixed", depots=2,
          depot_placement="hub_and_edge", classes=base, profiles=("light", "heavy"), fleet_tightness_pct=220,
          trip_fill_pct=45, optional_pct=10, release_pct=10, group_member_pct=10, shipment_permille=40,
          optional_shipment_pct=50, shared_location_pct=15, restricted_pct=6, bulky_pct=4, reload_policy="any"),
        s("towns-80", 9102, 74, geography="clustered", area_km=30, tw_regime="banded", depots=2,
          depot_placement="anchors", classes=base, profiles=("light", "heavy"), fleet_tightness_pct=210,
          trip_fill_pct=45, optional_pct=8, release_pct=8, group_member_pct=8, shipment_permille=35,
          shared_location_pct=12, restricted_pct=5, bulky_pct=4, overtime_policy="strict"),
        s("metro-100", 9103, 90, geography="core_satellites", area_km=26, tw_regime="tight", depots=3,
          depot_placement="anchors", classes=("cargo_bike",) + base, profiles=("light", "heavy", "bike"),
          load_dims=3, fleet_tightness_pct=220, trip_fill_pct=45, optional_pct=10, release_pct=10,
          group_member_pct=8, shipment_permille=35, shared_location_pct=15, restricted_pct=5, bulky_pct=3,
          reload_policy="nearest_two", open_route_classes=("van", "box_truck"), preloaded_vehicles=3),
        s("valley-120", 9104, 110, geography="corridor", area_km=40, tw_regime="loose", depots=2,
          depot_placement="spread", classes=base + ("express_van",), profiles=("light", "heavy", "express"),
          fleet_tightness_pct=200, trip_fill_pct=55, asymmetry_permille=120, service="long", optional_pct=10,
          release_pct=6, group_member_pct=6, shipment_permille=30, shared_location_pct=8, restricted_pct=4,
          bulky_pct=3, reload_policy="nearest_two", overtime_policy="generous",
          open_route_classes=("express_van",)),
        s("lake-140", 9105, 130, geography="ring", area_km=28, tw_regime="tight", depots=2,
          depot_placement="perimeter", classes=base, profiles=("light", "heavy"), fleet_tightness_pct=200,
          trip_fill_pct=45, asymmetry_permille=100, service="short", pickup_pct=18, backhaul_pct=5,
          optional_pct=6, release_pct=12, group_member_pct=6, shipment_permille=30, shared_location_pct=12,
          restricted_pct=4, bulky_pct=3, open_route_classes=("van",)),
        s("cities-160", 9106, 150, geography="multi_city", area_km=60, tw_regime="banded", depots=3,
          depot_placement="anchors", classes=base + ("heavy_truck",), profiles=("light", "heavy"),
          fleet_tightness_pct=220, fixed_cost_pct=120, trip_fill_pct=55, service="heavy_tail", optional_pct=10,
          release_pct=5, group_member_pct=5, shipment_permille=25, shared_location_pct=8, restricted_pct=3,
          bulky_pct=3, overtime_policy="generous", open_route_classes=("heavy_truck",)),
        s("suburbs-180", 9107, 168, geography="uniform", area_km=32, tw_regime="banded", depots=2,
          depot_placement="spread", classes=base, profiles=("light", "heavy"), fleet_tightness_pct=190,
          fixed_cost_pct=80, trip_fill_pct=50, service="mixed", pickup_pct=12, backhaul_pct=8, optional_pct=8,
          release_pct=8, group_member_pct=5, shipment_permille=30, shared_location_pct=10, restricted_pct=3,
          bulky_pct=2, reload_policy="any"),
        s("market-200", 9108, 186, geography="clustered", area_km=24, tw_regime="mixed", depots=3,
          depot_placement="anchors", classes=("cargo_bike",) + base, profiles=("light", "heavy", "bike"),
          load_dims=3, fleet_tightness_pct=200, trip_fill_pct=45, service="short", pickup_pct=14,
          optional_pct=8, release_pct=10, group_member_pct=5, shipment_permille=35, shared_location_pct=18,
          restricted_pct=5, bulky_pct=2, reload_policy="any", open_route_classes=("box_truck",)),
        s("region-220", 9109, 204, geography="core_satellites", area_km=40, tw_regime="mixed", depots=3,
          depot_placement="anchors", classes=base + ("heavy_truck",), profiles=("light", "heavy"),
          fleet_tightness_pct=180, fixed_cost_pct=110, trip_fill_pct=50, service="mixed", optional_pct=7,
          release_pct=7, group_member_pct=4, shipment_permille=35, shared_location_pct=12, restricted_pct=4,
          bulky_pct=2, reload_policy="nearest_two", preloaded_vehicles=3),
        s("county-240", 9110, 220, geography="corridor", area_km=45, tw_regime="tight", depots=3,
          depot_placement="spread", classes=base, profiles=("light", "heavy"), fleet_tightness_pct=190,
          trip_fill_pct=50, asymmetry_permille=80, service="short", pickup_pct=10, backhaul_pct=10,
          optional_pct=8, release_pct=6, group_member_pct=4, shipment_permille=40, shared_location_pct=10,
          restricted_pct=3, bulky_pct=2, reload_policy="any", open_route_classes=("box_truck",)),
    )


def tag(inst: dict, seed: int) -> dict:
    """The seeded tag pass: `tags` on clients, shipments and vehicle types."""
    rng = random.Random(TAG_SEED + seed)
    cold = len(inst["vehicle_types"][0]["capacity"]) > 2
    for client in inst["clients"]:
        tags = []
        if rng.random() < 0.08:
            tags.append("vip")
        if (cold and client["delivery"][2] > 0) or (not cold and rng.random() < 0.10):
            tags.append("frozen")
        if rng.random() < 0.06:
            tags.append("fragile")
        if rng.random() < 0.30:
            tags.append("business")
        if tags:
            client["tags"] = tags
    for shipment in inst["shipments"]:
        if rng.random() < 0.35:
            shipment["tags"] = ["express"]
    for vt in inst["vehicle_types"]:
        tags = []
        if vt["class"] in ("cargo_bike", "evening_van") or (vt["class"] == "van" and rng.random() < 0.3):
            tags.append("electric")
        if vt["class"] in ("box_truck", "heavy_truck") and rng.random() < 0.35:
            tags.append("rented")
        if cold and vt["capacity"][2] > 0 and vt["class"] != "cargo_bike":
            tags.append("cooled")
        if tags:
            vt["tags"] = tags
    return inst


def write(out_dir: Path = HERE) -> list[str]:
    """Write the ten samples into `out_dir`; one line about each."""
    g = _generator()
    lines = []
    for scen in scenarios(g):
        inst = tag(g.generate(scen, "harness-samples"), scen.seed)
        inst["format"] = "pyvrp-request/1"
        inst["time_zero"] = inst["meta"]["units"]["time_zero"]
        inst["cost_unit"] = 0.0001
        tasks = len(inst["clients"]) + 2 * len(inst["shipments"])
        text = g.dumps(inst)
        # Binary: text mode on Windows would turn "\n" into "\r\n".
        (Path(out_dir) / f"{scen.name}.json").write_bytes(text.encode("ascii"))
        lines.append(f"{scen.name}.json: {tasks} tasks, {sum(vt['num_available'] for vt in inst['vehicle_types'])} "
                     f"vehicles in {len(inst['vehicle_types'])} types, {len(text):,} bytes")
    return lines


if __name__ == "__main__":
    print("\n".join(write()))
