"""Deterministic generator for the `pyvrp_hard` benchmark instances.

    python generate.py --set tuning --out instances/
    python generate.py --set fresh  --out instances/
    python generate.py --set smoke  --out smoke/

Why this file exists
--------------------
A solver-tuning benchmark is only worth something if (a) anyone can rebuild
the exact instances, (b) the instances are not ten copies of one shape, and
(c) every modelling feature of the solver is switched on at the same time, so
that a parameter setting cannot win by being good at plain CVRP. This module
is the single source of truth for all three. Nothing is downloaded and nothing
is real-world data: every number is derived from `random.Random(seed)` and a
row of the scenario table below.

Reproducibility rules (they are the reason for some odd-looking code)
---------------------------------------------------------------------
* `random.Random(seed)` is the only randomness, and only its `random`,
  `randint`, `randrange`, `choice`, `sample` and `shuffle` methods are used.
* No transcendental function is ever called: no `gauss`, `cos`, `log`, `**`
  with float exponents. Those go through the platform's libm, which is not
  required to be correctly rounded, so a last-bit difference could flip a
  rounded coordinate on another machine. Only `+ - * /` and `math.sqrt` are
  used on floats; IEEE 754 requires all five to be correctly rounded, so the
  results are bit-identical everywhere. Normal deviates are an Irwin-Hall sum
  of twelve uniforms, angles are avoided by rejection sampling.
* Every stored number is an `int`. Travel matrices are NOT stored; they are a
  closed-form function of the integer coordinates and the integer per-profile
  parameters (see `edge`), and `instance.py` evaluates the same formula with
  numpy. That keeps a 3,000-client file at a few hundred kilobytes.
* The JSON is written with sorted keys, fixed separators, ASCII only and
  `\n` newlines, so a regenerated file is byte-identical.

Units
-----
distance  metre            duration  second (t = 0 is 06:00)
load 0    kilogram         load 1    cubic decimetre      load 2  cold boxes
cost      1e-4 currency units (a hundredth of a cent)

The cost unit is squeezed from two sides. PyVRP's `unit_distance_cost` and
`unit_duration_cost` are integers per metre and per second, so 0.30 per km
needs a unit finer than a cent: at 1e-4 it is the integer 3. From the other
side PyVRP clamps its violation penalties to `PenaltyParams.max_penalty`
(100,000 per unit of violation by default, starting from the midpoint
50,000): at 1e-4 that ceiling is worth 10 currency units per second of time
warp against a van that costs 100 to put on the road, which still bites. A
unit of 1e-6 would make the same ceiling 0.10 per second and the search would
happily trade a minute of lateness for a vehicle.

Feasibility by construction
---------------------------
After everything is drawn, every client and every shipment is checked against
a conservative single-stop route model (`_singleton_failure`): some vehicle
type must be able to leave its start depot no later than `start_late`, reach
the stop inside its time window given the release time, serve it, and be back
at its end depot before the shift window and the depot close, within
`max_distance` and the nominal `shift_duration` (no overtime), with the
demand fitting the vehicle and the location open to the vehicle's routing
profile. A stop that fails gets its release time dropped and its window moved
-- deterministically, no re-drawing -- and the number of such repairs is
recorded in `meta.counts`. Fleet-level feasibility cannot be proven this way;
`verify.py` establishes it by solving.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import random
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

__all__ = [
    "FORMAT",
    "FORBIDDEN",
    "GEOGRAPHIES",
    "TW_REGIMES",
    "Scenario",
    "SMOKE_SCENARIOS",
    "TUNING_SCENARIOS",
    "dumps",
    "edge",
    "fresh_scenarios",
    "generate",
    "generate_set",
    "main",
    "scenarios",
]

HERE = Path(__file__).resolve().parent

FORMAT = "pyvrp-hard/1"
"""Bumped whenever a regenerated file would differ. Part of every instance."""

FORBIDDEN = 10_000_000
"""Distance (m) and duration (s) of an edge a routing profile may not use.

PyVRP's own VRPLIB reader models "this vehicle may not visit that client" by
giving the vehicle's profile a huge value on every edge touching the client.
It uses `MAX_VALUE = 2**44`; we do not, because these instances also carry
`max_distance` and time windows, and 2**44 seconds of time warp multiplied by
a penalty of 1e5 is within a factor of five of overflowing int64. Ten thousand
kilometres is already 20x any `max_distance` in the benchmark, so one such
edge makes a route infeasible, and leaves twelve orders of magnitude of
headroom."""

HORIZON_S = 50_400
"""20:00. No client window ends later. Depots stay open 1-2 h longer so the
last slot and the return deadline are not the same instant."""

MARGIN_S = 900
"""Slack demanded by the single-stop feasibility model, so an instance is not
feasible only to the second."""

GEOGRAPHIES = (
    "uniform",
    "clustered",
    "core_satellites",
    "corridor",
    "ring",
    "multi_city",
)
TW_REGIMES = ("tight", "loose", "banded", "mixed")
DEPOT_PLACEMENTS = ("anchors", "perimeter", "spread", "hub_and_edge")
SERVICE_KINDS = ("short", "long", "mixed", "heavy_tail")
RELOAD_POLICIES = ("own", "nearest_two", "any")
OVERTIME_POLICIES = ("standard", "generous", "strict")


# -- the scenario table -----------------------------------------------------


@dataclass(frozen=True)
class Scenario:
    """One row of the scenario table: everything that shapes an instance.

    Percentages are integers (`_pct`), finer shares per mille (`_permille`).
    """

    name: str
    seed: int
    clients: int
    """Number of `Client` objects, group members included."""
    geography: str
    area_km: int
    """Side of the square the instance lives in. 25 is a city, 150 a region;
    speeds scale with it, `max_distance` and shift limits only partly, so they
    bind differently."""
    tw_regime: str
    depots: int
    depot_placement: str
    classes: tuple[str, ...]
    """Vehicle classes on offer, from `VEHICLE_CLASSES`. `van` (the class that
    may go everywhere) and at least one heavy class are mandatory."""
    profiles: tuple[str, ...]
    """Routing profiles, from `PROFILES`. `light` and `heavy` are mandatory; a
    third one only on instances of at most 1,800 clients (RAM)."""
    load_dims: int
    fleet_tightness_pct: int
    """Fleet size relative to a deliberately rough estimate of need. 140 is
    tight, 250 is roomy. Always feasible -- that is what `verify.py` is for."""
    fixed_cost_pct: int
    """Scales every fixed vehicle cost. High values reward reloads and
    overtime over an extra vehicle; low values the opposite."""
    trip_fill_pct: int
    """Demand calibration: a van load lasts for this share of the stops a van
    could serve in a shift if only time mattered. Low values force reloads."""
    asymmetry_permille: int
    """Strength of the directional drift of the most asymmetric profile: going
    with the drift vector costs this much more distance, against it less."""
    service: str
    pickup_pct: int
    """Clients with simultaneous pickup and delivery."""
    backhaul_pct: int
    """Pure pickups (no delivery)."""
    optional_pct: int
    """Prize-collecting clients outside groups."""
    release_pct: int
    group_member_pct: int
    """Share of the clients that are members of a mutually exclusive group."""
    optional_group_pct: int
    """Share of the groups that are optional (prize-collecting)."""
    shipment_permille: int
    """Pickup-and-delivery shipments per 1,000 clients."""
    optional_shipment_pct: int
    shared_location_pct: int
    """Target share of clients that share their location with another client
    (the newcomer and its host both count). Same-address group members come
    on top, so the measured share runs a point or two above this."""
    restricted_pct: int
    """Share of client locations the restricted (heavy) profiles may not visit."""
    bulky_pct: int
    """Clients whose demand exceeds a van: they need a heavy vehicle."""
    reload_policy: str
    overtime_policy: str
    open_route_classes: tuple[str, ...]
    """Classes of which a third of the vehicles end at a different depot."""
    preloaded_vehicles: int
    """Vehicles with an `initial_load` to drop at a depot."""


def _s(name: str, seed: int, clients: int, **kw) -> Scenario:
    defaults = dict(
        load_dims=2,
        fleet_tightness_pct=170,
        fixed_cost_pct=100,
        trip_fill_pct=50,
        asymmetry_permille=60,
        service="mixed",
        pickup_pct=10,
        backhaul_pct=6,
        optional_pct=6,
        release_pct=6,
        group_member_pct=3,
        optional_group_pct=40,
        shipment_permille=10,
        optional_shipment_pct=40,
        shared_location_pct=10,
        restricted_pct=3,
        bulky_pct=2,
        reload_policy="own",
        overtime_policy="standard",
        open_route_classes=("box_truck",),
        preloaded_vehicles=2,
    )
    defaults.update(kw)
    return Scenario(name=name, seed=seed, clients=clients, **defaults)


TUNING_SCENARIOS: tuple[Scenario, ...] = (
    # t01 -- a compact city under one- and two-hour delivery slots. Cargo
    # bikes on their own routing profile, three load dimensions, a fifth of
    # the clients in shared buildings. Small distances: time windows and the
    # bikes' 45 km range bind, max_distance of the motor vehicles does not.
    _s(
        "t01-n1000-uniform-city-tight", 1101, 1000,
        geography="uniform", area_km=25, tw_regime="tight",
        depots=2, depot_placement="hub_and_edge",
        classes=("cargo_bike", "van", "box_truck", "evening_van"),
        profiles=("light", "heavy", "bike"), load_dims=3,
        fleet_tightness_pct=180, trip_fill_pct=45, asymmetry_permille=40,
        service="short", pickup_pct=15, backhaul_pct=5, optional_pct=8,
        release_pct=10, group_member_pct=4, optional_group_pct=50,
        shipment_permille=20, shared_location_pct=20, restricted_pct=4,
        reload_policy="any", open_route_classes=("box_truck",),
    ),
    # t02 -- eight Gaussian towns in a 60 km square, morning / afternoon /
    # evening delivery bands, one depot in each of the three largest towns.
    # Strict overtime and expensive vehicles: reloading beats a second van.
    _s(
        "t02-n1200-clustered-banded", 1202, 1200,
        geography="clustered", area_km=60, tw_regime="banded",
        depots=3, depot_placement="anchors",
        classes=("van", "box_truck", "heavy_truck", "evening_van"),
        profiles=("light", "heavy"), load_dims=3,
        fleet_tightness_pct=160, fixed_cost_pct=120, trip_fill_pct=40,
        asymmetry_permille=80, service="mixed", pickup_pct=10,
        backhaul_pct=8, optional_pct=5, release_pct=6, group_member_pct=3,
        shipment_permille=10, shared_location_pct=10, restricted_pct=3,
        reload_policy="own", overtime_policy="strict",
        open_route_classes=("heavy_truck",), preloaded_vehicles=3,
    ),
    # t03 -- a 120 km valley: towns strung along a bent corridor, all-day
    # windows, long B2B service times, a strong one-way drift along the
    # valley and an express profile that is fast between towns and clumsy
    # inside them. Distance limits and shift lengths bind here.
    _s(
        "t03-n1400-corridor-loose", 1303, 1400,
        geography="corridor", area_km=120, tw_regime="loose",
        depots=3, depot_placement="spread",
        classes=("van", "box_truck", "express_van"),
        profiles=("light", "heavy", "express"),
        fleet_tightness_pct=200, fixed_cost_pct=90, trip_fill_pct=60,
        asymmetry_permille=150, service="long", pickup_pct=5,
        backhaul_pct=12, optional_pct=10, release_pct=4, group_member_pct=2,
        shipment_permille=5, shared_location_pct=5, restricted_pct=2,
        reload_policy="nearest_two", overtime_policy="generous",
        open_route_classes=("express_van", "box_truck"),
    ),
    # t04 -- the classic metro: dense core, satellite towns, rural fringe;
    # every window shape at once; all five vehicle classes on three profiles
    # and three load dimensions; four depots, vans may reload anywhere.
    _s(
        "t04-n1600-metro-mixed", 1404, 1600,
        geography="core_satellites", area_km=40, tw_regime="mixed",
        depots=4, depot_placement="anchors",
        classes=("cargo_bike", "van", "box_truck", "heavy_truck", "evening_van"),
        profiles=("light", "heavy", "bike"), load_dims=3,
        fleet_tightness_pct=170, trip_fill_pct=50, asymmetry_permille=60,
        service="mixed", pickup_pct=12, backhaul_pct=6, optional_pct=6,
        release_pct=8, group_member_pct=5, shipment_permille=12,
        shared_location_pct=15, restricted_pct=5, reload_policy="any",
        open_route_classes=("van",), preloaded_vehicles=4,
    ),
    # t05 -- four cities 50-100 km apart with a depot each, banded windows,
    # heavy-tailed service times. Trucks and express vans may run one-way
    # between cities (start depot != end depot) -- inter-city line haul.
    _s(
        "t05-n1800-multicity-banded", 1505, 1800,
        geography="multi_city", area_km=150, tw_regime="banded",
        depots=4, depot_placement="anchors",
        classes=("van", "box_truck", "heavy_truck", "express_van"),
        profiles=("light", "heavy", "express"),
        fleet_tightness_pct=220, fixed_cost_pct=130, trip_fill_pct=55,
        asymmetry_permille=100, service="heavy_tail", pickup_pct=8,
        backhaul_pct=10, optional_pct=12, release_pct=5, group_member_pct=3,
        shipment_permille=8, shared_location_pct=8, restricted_pct=2,
        reload_policy="own", overtime_policy="generous",
        open_route_classes=("heavy_truck", "express_van"),
        preloaded_vehicles=3,
    ),
    # t06 -- a ring city around a lake: nobody lives in the middle, depots on
    # the perimeter, tight slots, a fifth of the clients hand something back,
    # many late releases. Cheap vehicles: an extra van beats overtime.
    _s(
        "t06-n2000-ring-tight", 1606, 2000,
        geography="ring", area_km=50, tw_regime="tight",
        depots=3, depot_placement="perimeter",
        classes=("van", "box_truck", "evening_van"),
        profiles=("light", "heavy"),
        fleet_tightness_pct=190, fixed_cost_pct=80, trip_fill_pct=45,
        asymmetry_permille=120, service="short", pickup_pct=20,
        backhaul_pct=4, optional_pct=5, release_pct=12, group_member_pct=4,
        shipment_permille=15, shared_location_pct=12, restricted_pct=3,
        reload_policy="nearest_two", open_route_classes=("van",),
    ),
    # t07 -- ten towns over 100 km, five depots, every window shape, long
    # service times, many pure backhauls and many optional clients, the
    # tightest fleet of the set and the most expensive vehicles.
    _s(
        "t07-n2200-clustered-region-mixed", 1707, 2200,
        geography="clustered", area_km=100, tw_regime="mixed",
        depots=5, depot_placement="anchors",
        classes=("van", "box_truck", "heavy_truck", "evening_van"),
        profiles=("light", "heavy"),
        fleet_tightness_pct=150, fixed_cost_pct=150, trip_fill_pct=55,
        asymmetry_permille=50, service="long", pickup_pct=6,
        backhaul_pct=15, optional_pct=15, release_pct=3, group_member_pct=2,
        shipment_permille=6, shared_location_pct=6, restricted_pct=2,
        reload_policy="any", overtime_policy="strict",
        open_route_classes=("box_truck",), preloaded_vehicles=5,
    ),
    # t08 -- 2,500 clients scattered uniformly over 80 km, all-day windows,
    # a roomy fleet of three classes, nearly symmetric roads. The plainest
    # instance of the set; it is here so that "good at the odd ones" is not
    # the same as "good".
    _s(
        "t08-n2500-uniform-region-loose", 1808, 2500,
        geography="uniform", area_km=80, tw_regime="loose",
        depots=4, depot_placement="spread",
        classes=("van", "box_truck", "heavy_truck"),
        profiles=("light", "heavy"),
        fleet_tightness_pct=250, trip_fill_pct=60, asymmetry_permille=30,
        service="mixed", pickup_pct=10, backhaul_pct=10, optional_pct=10,
        release_pct=7, group_member_pct=3, shipment_permille=10,
        shared_location_pct=10, restricted_pct=3, reload_policy="own",
        overtime_policy="generous", open_route_classes=("heavy_truck",),
        preloaded_vehicles=3,
    ),
    # t09 -- a large metro (70 km) with five depots, banded windows,
    # heavy-tailed service, many shipments and many shared locations.
    _s(
        "t09-n2800-metro-banded", 1909, 2800,
        geography="core_satellites", area_km=70, tw_regime="banded",
        depots=5, depot_placement="anchors",
        classes=("van", "box_truck", "heavy_truck", "evening_van"),
        profiles=("light", "heavy"),
        fleet_tightness_pct=170, fixed_cost_pct=110, trip_fill_pct=50,
        asymmetry_permille=90, service="heavy_tail", pickup_pct=14,
        backhaul_pct=7, optional_pct=7, release_pct=9, group_member_pct=4,
        shipment_permille=18, shared_location_pct=18, restricted_pct=4,
        reload_policy="nearest_two", open_route_classes=("van", "box_truck"),
        preloaded_vehicles=4,
    ),
    # t10 -- the largest: 3,000 clients along a 90 km corridor with five
    # depots strung along it and every window shape.
    _s(
        "t10-n3000-corridor-mixed", 2010, 3000,
        geography="corridor", area_km=90, tw_regime="mixed",
        depots=5, depot_placement="spread",
        classes=("van", "box_truck", "heavy_truck", "evening_van"),
        profiles=("light", "heavy"),
        fleet_tightness_pct=180, trip_fill_pct=50, asymmetry_permille=70,
        service="short", pickup_pct=12, backhaul_pct=8, optional_pct=8,
        release_pct=6, group_member_pct=3, shipment_permille=10,
        shared_location_pct=10, restricted_pct=3, reload_policy="any",
        open_route_classes=("box_truck",), preloaded_vehicles=4,
    ),
)

SMOKE_SCENARIOS: tuple[Scenario, ...] = (
    # Tiny instances for tests and demos. Same generator, same features.
    _s(
        "s01-n60-uniform-mixed", 61, 60,
        geography="uniform", area_km=20, tw_regime="mixed",
        depots=2, depot_placement="hub_and_edge",
        classes=("van", "box_truck", "evening_van"),
        profiles=("light", "heavy"),
        fleet_tightness_pct=220, trip_fill_pct=45, pickup_pct=12,
        backhaul_pct=8, optional_pct=12, release_pct=10,
        group_member_pct=10, optional_group_pct=50, shipment_permille=50,
        optional_shipment_pct=50, shared_location_pct=15, restricted_pct=6,
        bulky_pct=4, reload_policy="any",
    ),
    _s(
        "s02-n120-metro-tight", 122, 120,
        geography="core_satellites", area_km=30, tw_regime="tight",
        depots=3, depot_placement="anchors",
        classes=("cargo_bike", "van", "box_truck", "evening_van"),
        profiles=("light", "heavy", "bike"), load_dims=3,
        fleet_tightness_pct=220, trip_fill_pct=45, pickup_pct=12,
        backhaul_pct=8, optional_pct=10, release_pct=10,
        group_member_pct=8, optional_group_pct=50, shipment_permille=35,
        optional_shipment_pct=50, shared_location_pct=15, restricted_pct=5,
        bulky_pct=3, reload_policy="nearest_two",
        open_route_classes=("van", "box_truck"), preloaded_vehicles=3,
    ),
)

FRESH_META_SEED = 20_260_919
"""Seeds the draw of the fresh scenarios' parameters. The fresh set exists to
check that a tuned configuration generalises, so its rows are drawn from the
same families rather than written by the person who wrote the tuning rows."""

FRESH_SIZES = (1100, 1700, 2300, 2900)

_AREA_RANGE_KM = {
    "uniform": (25, 90),
    "clustered": (40, 110),
    "core_satellites": (35, 80),
    "corridor": (80, 140),
    "ring": (40, 70),
    "multi_city": (110, 150),
}
_PLACEMENTS_FOR = {
    "uniform": ("spread", "hub_and_edge", "perimeter"),
    "clustered": ("anchors", "spread"),
    "core_satellites": ("anchors", "hub_and_edge"),
    "corridor": ("spread", "anchors"),
    "ring": ("perimeter", "spread", "anchors"),
    "multi_city": ("anchors",),
}


def fresh_scenarios() -> tuple[Scenario, ...]:
    """Four validation scenarios with freshly drawn parameters.

    Deterministic (seeded by `FRESH_META_SEED`), but not hand-written: the
    geography, area, window regime, fleet composition and every share are
    drawn from the same ranges the tuning rows were picked from. Geographies
    and window regimes are sampled without replacement, so the four differ.
    """
    rng = random.Random(FRESH_META_SEED)
    geographies = rng.sample(GEOGRAPHIES, len(FRESH_SIZES))
    regimes = rng.sample(TW_REGIMES, len(FRESH_SIZES))
    rows = []
    for idx, size in enumerate(FRESH_SIZES):
        geography = geographies[idx]
        lo, hi = _AREA_RANGE_KM[geography]
        area_km = 5 * rng.randint(lo // 5, hi // 5)
        depots = rng.randint(3, 5) if geography == "multi_city" else rng.randint(2, 5)
        profiles: tuple[str, ...] = ("light", "heavy")
        if size <= 1800:
            profiles += ("bike",) if area_km <= 45 else ("express",)
        extras = ["heavy_truck", "evening_van"]
        if "bike" in profiles:
            extras.append("cargo_bike")
        if "express" in profiles:
            extras.append("express_van")
        chosen = rng.sample(extras, rng.randint(1, min(3, len(extras))))
        classes = ("van", "box_truck") + tuple(
            name for name in VEHICLE_CLASSES if name in chosen
        )
        # The third profile is only OFFERED above; keep it only if a sampled
        # class actually drives on it. A first draft kept it regardless, and
        # f02 shipped two 1,700 x 1,700 express matrices that no vehicle
        # used. No randomness is consumed here, so the other rows' draws
        # are unaffected.
        profiles = tuple(
            p for p in profiles
            if any(VEHICLE_CLASSES[c].profile == p for c in classes)
        )
        open_pool = [c for c in classes if c not in ("cargo_bike", "evening_van")]
        rows.append(
            Scenario(
                name=f"f{idx + 1:02d}-n{size}-{geography.replace('_', '')}-{regimes[idx]}",
                seed=rng.randrange(1_000_000),
                clients=size,
                geography=geography,
                area_km=area_km,
                tw_regime=regimes[idx],
                depots=depots,
                depot_placement=rng.choice(_PLACEMENTS_FOR[geography]),
                classes=classes,
                profiles=profiles,
                load_dims=3 if size <= 1800 and rng.random() < 0.5 else 2,
                fleet_tightness_pct=10 * rng.randint(15, 25),
                fixed_cost_pct=10 * rng.randint(8, 15),
                trip_fill_pct=5 * rng.randint(8, 12),
                asymmetry_permille=10 * rng.randint(3, 15),
                service=rng.choice(SERVICE_KINDS),
                pickup_pct=rng.randint(5, 20),
                backhaul_pct=rng.randint(4, 15),
                optional_pct=rng.randint(5, 15),
                release_pct=rng.randint(3, 12),
                group_member_pct=rng.randint(2, 5),
                optional_group_pct=10 * rng.randint(3, 6),
                shipment_permille=rng.randint(5, 20),
                optional_shipment_pct=10 * rng.randint(3, 6),
                shared_location_pct=rng.randint(5, 20),
                restricted_pct=rng.randint(2, 5),
                bulky_pct=rng.randint(1, 3),
                reload_policy=rng.choice(RELOAD_POLICIES),
                overtime_policy=rng.choice(OVERTIME_POLICIES),
                open_route_classes=tuple(
                    sorted(rng.sample(open_pool, rng.randint(1, 2)))
                ),
                preloaded_vehicles=rng.randint(2, 5),
            )
        )
    return tuple(rows)


def scenarios(which: str) -> tuple[Scenario, ...]:
    """The scenario rows of one set: `tuning`, `fresh` or `smoke`."""
    if which == "tuning":
        return TUNING_SCENARIOS
    if which == "fresh":
        return fresh_scenarios()
    if which == "smoke":
        return SMOKE_SCENARIOS
    raise ValueError(f"unknown set {which!r}; expected tuning, fresh or smoke")


# -- routing profiles -------------------------------------------------------

PROFILES: dict[str, dict[str, int | bool]] = {
    # `detour_permille`: road distance over crow-fly distance.
    # `speed_mm_s`: door-to-door speed in a 40 km metro; scaled with the area.
    # `stop_overhead_s`: parking and walking, paid on every edge between two
    #   different locations -- which is what makes a shared location cheap.
    # `zone_in` / `zone_out`: extra distance on an edge that ends / starts in
    #   the congestion zone. Unequal values are a second source of asymmetry.
    # `zone_slow`: extra travel time on any edge touching the zone.
    # `drift_pct`: share of the scenario's asymmetry this profile feels.
    # `restricted`: may not visit the instance's restricted locations.
    "light": dict(
        detour_permille=1300, speed_mm_s=8500, stop_overhead_s=45,
        zone_in_permille=0, zone_out_permille=0, zone_slow_permille=200,
        drift_pct=50, restricted=False, scales_with_area=True,
    ),
    "heavy": dict(
        detour_permille=1420, speed_mm_s=7000, stop_overhead_s=90,
        zone_in_permille=180, zone_out_permille=60, zone_slow_permille=350,
        drift_pct=100, restricted=True, scales_with_area=True,
    ),
    "bike": dict(
        detour_permille=1180, speed_mm_s=4600, stop_overhead_s=15,
        zone_in_permille=0, zone_out_permille=0, zone_slow_permille=0,
        drift_pct=20, restricted=False, scales_with_area=False,
    ),
    "express": dict(
        detour_permille=1500, speed_mm_s=12500, stop_overhead_s=150,
        zone_in_permille=100, zone_out_permille=100, zone_slow_permille=500,
        drift_pct=80, restricted=False, scales_with_area=True,
    ),
}

_COMPASS = (
    (1000, 0), (707, 707), (0, 1000), (-707, 707),
    (-1000, 0), (-707, -707), (0, -1000), (707, -707),
)
"""Unit vectors in per mille. A table instead of cos/sin: see the module
docstring on transcendental functions."""


# -- vehicle classes --------------------------------------------------------


@dataclass(frozen=True)
class VehicleClass:
    """Catalogue entry. Costs in 1e-4 currency units, capacities per dimension
    (kg, dm3, cold boxes), durations in seconds.

    `fixed_cost` is a vehicle-DAY: the vehicle plus the driver's guaranteed
    base pay, about 100 for a van, against 21.60 per hour on the road. A first
    draft charged 35 and 25.20 -- then a second van is always cheaper than an
    hour of overtime or a trip back to the depot, no solution ever contains
    either, and two of the features under test are dead weight."""

    profile: str
    capacity: tuple[int, int, int]
    fixed_cost: int
    unit_distance_cost: int
    unit_duration_cost: int
    shift_s: int
    max_distance_km: int
    """At a 40 km area; scaled with the square root of the area, floored so
    that every location stays reachable from its nearest depot."""
    max_reloads: int
    overtime_s: int
    shift_start_s: int
    start_window_s: int
    """`start_late - tw_early`: how long the vehicle may delay its start."""
    evening: bool
    mix: int
    """Relative share of the stops this class is sized for."""


VEHICLE_CLASSES: dict[str, VehicleClass] = {
    "cargo_bike": VehicleClass(
        profile="bike", capacity=(150, 1200, 6), fixed_cost=350_000,
        unit_distance_cost=1, unit_duration_cost=45, shift_s=21_600,
        max_distance_km=45, max_reloads=3, overtime_s=3_600,
        shift_start_s=3_600, start_window_s=14_400, evening=False, mix=12,
    ),
    "van": VehicleClass(
        profile="light", capacity=(900, 7_000, 24), fixed_cost=1_000_000,
        unit_distance_cost=3, unit_duration_cost=60, shift_s=27_000,
        max_distance_km=220, max_reloads=2, overtime_s=5_400,
        shift_start_s=0, start_window_s=21_600, evening=False, mix=45,
    ),
    "box_truck": VehicleClass(
        profile="heavy", capacity=(3_200, 22_000, 70), fixed_cost=1_350_000,
        unit_distance_cost=5, unit_duration_cost=70, shift_s=28_800,
        max_distance_km=320, max_reloads=1, overtime_s=3_600,
        shift_start_s=0, start_window_s=16_200, evening=False, mix=25,
    ),
    "heavy_truck": VehicleClass(
        profile="heavy", capacity=(8_000, 48_000, 160), fixed_cost=1_800_000,
        unit_distance_cost=7, unit_duration_cost=80, shift_s=34_200,
        max_distance_km=400, max_reloads=0, overtime_s=0,
        shift_start_s=0, start_window_s=10_800, evening=False, mix=10,
    ),
    "evening_van": VehicleClass(
        profile="light", capacity=(900, 7_000, 24), fixed_cost=900_000,
        unit_distance_cost=3, unit_duration_cost=70, shift_s=19_800,
        max_distance_km=180, max_reloads=1, overtime_s=3_600,
        shift_start_s=25_200, start_window_s=9_000, evening=True, mix=15,
    ),
    "express_van": VehicleClass(
        profile="express", capacity=(1_300, 10_000, 30), fixed_cost=1_200_000,
        unit_distance_cost=4, unit_duration_cost=65, shift_s=28_800,
        max_distance_km=450, max_reloads=1, overtime_s=5_400,
        shift_start_s=0, start_window_s=18_000, evening=False, mix=25,
    ),
}

DAY_SHIFT_END_S = 45_000
"""18:30. Where an instance has evening vans, the day classes must be back by
then, so the last delivery band genuinely needs the evening shift."""

RELEASE_WAVES_S = (9_000, 14_400, 19_800)
"""08:30, 10:00, 11:30: inbound trailers that are not there at 06:00."""

_TW_FACTOR_PCT = {"loose": 90, "banded": 75, "mixed": 70, "tight": 55}
"""Share of the time-only productivity a vehicle keeps under each window
regime (waiting and criss-crossing). Used for fleet sizing only."""

_DENSITY_PCT = {
    "uniform": 100, "clustered": 30, "core_satellites": 40,
    "corridor": 15, "ring": 35, "multi_city": 12,
}
"""Share of the square that is actually populated; shortens the estimated
distance between neighbouring stops. Used for fleet sizing only."""


class GenerationError(RuntimeError):
    """A scenario row that cannot be turned into a feasible-by-construction
    instance. Always a bug in the row or the generator, never bad luck."""


# -- deterministic random helpers -------------------------------------------


def _gauss(rng: random.Random) -> float:
    """Approximately N(0, 1): twelve uniforms minus six. Additions only."""
    return sum(rng.random() for _ in range(12)) - 6.0


def _skew(rng: random.Random) -> float:
    """In (0, 1], mean about 0.29, long right tail. `u * u * u`, not `u ** 3`:
    `pow` is a libm call, multiplication is not."""
    u = rng.random()
    return 0.05 + 0.95 * u * u * u


def _clip(value: int, lo: int, hi: int) -> int:
    return lo if value < lo else hi if value > hi else value


def _dist2(a: tuple[int, int], b: tuple[int, int]) -> int:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


def _spaced_points(
    rng: random.Random, count: int, lo: int, hi: int, min_sep: int
) -> list[tuple[int, int]]:
    """`count` points in [lo, hi]^2 at least `min_sep` apart. The separation
    is relaxed by 10 % after every 200 rejections, so this always ends."""
    points: list[tuple[int, int]] = []
    sep, misses = min_sep, 0
    while len(points) < count:
        cand = (rng.randint(lo, hi), rng.randint(lo, hi))
        if all(_dist2(cand, p) >= sep * sep for p in points):
            points.append(cand)
            continue
        misses += 1
        if misses % 200 == 0:
            sep = sep * 9 // 10
    return points


def _annulus_point(
    rng: random.Random, centre: tuple[int, int], r_in: int, r_out: int, side: int
) -> tuple[int, int]:
    """Uniform in a ring around `centre`, inside the square. Rejection from
    the bounding box instead of radius-and-angle: no trigonometry."""
    while True:
        x = rng.randint(centre[0] - r_out, centre[0] + r_out)
        y = rng.randint(centre[1] - r_out, centre[1] + r_out)
        if not (0 <= x <= side and 0 <= y <= side):
            continue
        if r_in * r_in <= _dist2((x, y), centre) <= r_out * r_out:
            return x, y


def _fps_order(points: list[tuple[int, int]], first: int = 0) -> list[int]:
    """Farthest-point ordering: each next index maximises its distance to the
    ones already chosen. Integer arithmetic, ties to the lowest index."""
    order = [first]
    best = [_dist2(p, points[first]) for p in points]
    while len(order) < len(points):
        nxt = max(range(len(points)), key=lambda i: (best[i], -i))
        if best[nxt] == 0:
            break
        order.append(nxt)
        for i, p in enumerate(points):
            d = _dist2(p, points[nxt])
            if d < best[i]:
                best[i] = d
    return order


# -- geography --------------------------------------------------------------


@dataclass
class _Geography:
    draw: Callable[[], tuple[int, int]]
    anchors: list[tuple[int, int]]
    """Natural depot sites: town centres, spread out, most important first."""
    zone_centre: tuple[int, int]
    """Centre of the congestion zone and of the restricted core."""


def _weighted_index(rng: random.Random, weights: list[int]) -> int:
    ticket = rng.randrange(sum(weights))
    for idx, weight in enumerate(weights):
        ticket -= weight
        if ticket < 0:
            return idx
    raise AssertionError("unreachable")


def _make_geography(kind: str, side: int, rng: random.Random) -> _Geography:
    mid = (side // 2, side // 2)

    def around(centre: tuple[int, int], sigma: float) -> tuple[int, int]:
        return (
            _clip(round(centre[0] + sigma * _gauss(rng)), 0, side),
            _clip(round(centre[1] + sigma * _gauss(rng)), 0, side),
        )

    def anywhere() -> tuple[int, int]:
        return rng.randint(0, side), rng.randint(0, side)

    if kind == "uniform":
        sites = [(50, 50), (20, 25), (80, 75), (20, 80), (80, 20)]
        anchors = [(side * a // 100, side * b // 100) for a, b in sites]
        return _Geography(anywhere, anchors, mid)

    if kind == "clustered":
        count = rng.randint(7, 10)
        centres = _spaced_points(
            rng, count, side * 12 // 100, side * 88 // 100, side * 16 // 100
        )
        weights = [50 + rng.randrange(150) for _ in centres]
        sigmas = [side * (25 + rng.randrange(30)) / 1000 for _ in centres]

        def draw_clustered() -> tuple[int, int]:
            if rng.random() < 0.10:
                return anywhere()
            k = _weighted_index(rng, weights)
            return around(centres[k], sigmas[k])

        by_weight = sorted(range(count), key=lambda k: (-weights[k], k))
        ordered = [centres[k] for k in by_weight]
        anchors = [ordered[i] for i in _fps_order(ordered)]
        return _Geography(draw_clustered, anchors, ordered[0])

    if kind == "core_satellites":
        core = (
            mid[0] + rng.randint(-side // 25, side // 25),
            mid[1] + rng.randint(-side // 25, side // 25),
        )
        towns: list[tuple[int, int]] = []
        sep, misses = side * 18 // 100, 0
        while len(towns) < 6:
            cand = _annulus_point(
                rng, core, side * 27 // 100, side * 43 // 100, side
            )
            if all(_dist2(cand, t) >= sep * sep for t in towns):
                towns.append(cand)
                continue
            misses += 1
            if misses % 200 == 0:
                sep = sep * 9 // 10

        def draw_metro() -> tuple[int, int]:
            u = rng.random()
            if u < 0.45:
                return around(core, side * 0.07)
            if u < 0.85:
                return around(towns[rng.randrange(len(towns))], side * 0.035)
            while True:  # rural fringe: really a fringe, not more suburb
                cand = anywhere()
                if _dist2(cand, core) >= (side * 30 // 100) ** 2:
                    return cand

        anchors = [core] + [towns[i] for i in _fps_order(towns)]
        return _Geography(draw_metro, anchors, core)

    if kind == "corridor":
        knots = [
            (side * 4 // 100, rng.randint(side * 30 // 100, side * 70 // 100)),
            (side * 50 // 100, rng.randint(side * 30 // 100, side * 70 // 100)),
            (side * 96 // 100, rng.randint(side * 30 // 100, side * 70 // 100)),
        ]

        def on_axis(t: float) -> tuple[int, int]:
            a, b, s = (knots[0], knots[1], 2 * t) if t < 0.5 else (
                knots[1], knots[2], 2 * t - 1)
            return (
                round(a[0] + (b[0] - a[0]) * s),
                round(a[1] + (b[1] - a[1]) * s),
            )

        count = rng.randint(5, 7)
        towns = [
            on_axis((k + 0.2 + 0.6 * rng.random()) / count) for k in range(count)
        ]
        weights = [50 + rng.randrange(150) for _ in towns]

        def draw_corridor() -> tuple[int, int]:
            u = rng.random()
            if u < 0.55:
                return around(towns[_weighted_index(rng, weights)], side * 0.018)
            base = on_axis(rng.random())
            if u < 0.90:
                return around(base, side * 0.025)
            return (
                _clip(base[0] + rng.randint(-side // 50, side // 50), 0, side),
                _clip(base[1] + rng.randint(-side * 12 // 100, side * 12 // 100),
                      0, side),
            )

        heaviest = max(range(count), key=lambda k: (weights[k], -k))
        anchors = [towns[i] for i in _fps_order(towns, first=heaviest)]
        return _Geography(draw_corridor, anchors, towns[heaviest])

    if kind == "ring":
        r_in, r_out = side * 30 // 100, side * 44 // 100
        hubs: list[tuple[int, int]] = []
        sep, misses = side * 25 // 100, 0
        while len(hubs) < 5:
            cand = _annulus_point(rng, mid, r_in, r_out, side)
            if all(_dist2(cand, h) >= sep * sep for h in hubs):
                hubs.append(cand)
                continue
            misses += 1
            if misses % 200 == 0:
                sep = sep * 9 // 10

        def draw_ring() -> tuple[int, int]:
            if rng.random() < 0.30:
                return around(hubs[rng.randrange(len(hubs))], side * 0.02)
            return _annulus_point(rng, mid, r_in, r_out, side)

        anchors = [hubs[i] for i in _fps_order(hubs)]
        return _Geography(draw_ring, anchors, hubs[0])

    if kind == "multi_city":
        count = rng.randint(4, 5)
        cities = _spaced_points(
            rng, count, side * 12 // 100, side * 88 // 100, side * 38 // 100
        )
        weights = [60 + rng.randrange(140) for _ in cities]
        sigmas = [side * (25 + rng.randrange(20)) / 1000 for _ in cities]

        def draw_cities() -> tuple[int, int]:
            if rng.random() < 0.08:
                return anywhere()
            k = _weighted_index(rng, weights)
            return around(cities[k], sigmas[k])

        by_weight = sorted(range(count), key=lambda k: (-weights[k], k))
        return _Geography(draw_cities, [cities[k] for k in by_weight],
                          cities[by_weight[0]])

    raise GenerationError(f"unknown geography {kind!r}")


# -- the travel model -------------------------------------------------------


def edge(
    a: tuple[int, int],
    b: tuple[int, int],
    a_in_zone: bool,
    b_in_zone: bool,
    profile: dict,
) -> tuple[int, int]:
    """Distance (m) and duration (s) from `a` to `b` under `profile`.

    THE normative travel model; `instance.build_matrices` evaluates the same
    expression, in the same order of operations, with numpy. For a != b:

        e     = sqrt(dx^2 + dy^2)                      dx = b.x - a.x, ...
        along = dx * drift_x + dy * drift_y            (integers, per mille)
        dist  = rint((e + along / 1000) * (detour / 1000)
                     * (1 + (zone_in * [b in zone] + zone_out * [a in zone]) / 1000))
        time  = rint(dist * 1000 / speed_mm_s
                     * (1 + zone_slow * [a or b in zone] / 1000)) + stop_overhead

    `along` makes the matrix asymmetric: travelling with the drift vector is
    longer than travelling against it, by up to twice the drift strength. Two
    different location indices never have zero travel time (the overhead),
    two clients on the SAME location index always do. Access restrictions are
    not applied here; see `FORBIDDEN`.
    """
    dx, dy = b[0] - a[0], b[1] - a[1]
    e = math.sqrt(dx * dx + dy * dy)
    drift = profile["drift_permille"]
    along = dx * drift[0] + dy * drift[1]
    zone = profile["zone_in_permille"] * int(b_in_zone) + profile[
        "zone_out_permille"
    ] * int(a_in_zone)
    dist = round(
        (e + along / 1000.0) * (profile["detour_permille"] / 1000.0)
        * (1.0 + zone / 1000.0)
    )
    slow = profile["zone_slow_permille"] * int(a_in_zone or b_in_zone)
    time = round(
        dist * 1000.0 / profile["speed_mm_s"] * (1.0 + slow / 1000.0)
    ) + profile["stop_overhead_s"]
    return dist, time


class _Travel:
    """Scalar travel queries on the instance being built, by location index."""

    def __init__(self, inst: dict):
        self.xy = [tuple(p) for p in inst["locations"]]
        zone = inst["zone"]
        r2 = zone["radius"] ** 2
        centre = (zone["x"], zone["y"])
        self.in_zone = [_dist2(p, centre) <= r2 for p in self.xy]
        self.restricted = set(inst["restricted_locations"])
        self.profiles = inst["profiles"]

    def __call__(self, i: int, j: int, p: int) -> tuple[int, int]:
        if i == j:
            return 0, 0
        profile = self.profiles[p]
        if profile["restricted"] and (i in self.restricted or j in self.restricted):
            return FORBIDDEN, FORBIDDEN
        return edge(self.xy[i], self.xy[j], self.in_zone[i], self.in_zone[j], profile)


# -- building blocks --------------------------------------------------------


def _windows(regime: str, rng: random.Random) -> tuple[int, int]:
    """One client time window (tw_early, tw_late) under `regime`."""
    morning, afternoon, evening = (7_200, 21_600), (21_600, 36_000), (36_000, HORIZON_S)
    all_day = (0, HORIZON_S)

    def tight() -> tuple[int, int]:
        width = rng.choice((3_600, 5_400, 7_200))
        start = 7_200 + 1_800 * rng.randrange((HORIZON_S - width - 7_200) // 1_800 + 1)
        return start, start + width

    def half() -> tuple[int, int]:
        return rng.choice(((7_200, 28_800), (28_800, HORIZON_S)))

    def wide() -> tuple[int, int]:
        width = rng.choice((10_800, 14_400))
        start = 7_200 + 3_600 * rng.randrange((HORIZON_S - width - 7_200) // 3_600 + 1)
        return start, start + width

    def band() -> tuple[int, int]:
        return (morning, afternoon, evening)[_weighted_index(rng, [40, 35, 25])]

    u = rng.randrange(100)
    if regime == "tight":
        return tight() if u < 60 else half() if u < 75 else band() if u < 85 else all_day
    if regime == "loose":
        return all_day if u < 65 else half() if u < 90 else wide()
    if regime == "banded":
        return band() if u < 80 else all_day
    if regime == "mixed":
        if u < 25:
            return tight()
        if u < 40:
            return wide()
        return band() if u < 65 else half() if u < 80 else all_day
    raise GenerationError(f"unknown time-window regime {regime!r}")


def _service(kind: str, rng: random.Random) -> int:
    """Service duration in seconds, a multiple of 10."""
    if kind == "short":
        seconds = 60 + rng.randrange(181)
    elif kind == "long":
        seconds = 300 + rng.randrange(901)
    elif kind == "mixed":
        seconds = 60 + rng.randrange(181) if rng.random() < 0.7 else 300 + rng.randrange(901)
    elif kind == "heavy_tail":
        u = rng.random()
        seconds = 120 + round(2_280 * u * u * u * u)
    else:
        raise GenerationError(f"unknown service kind {kind!r}")
    return 10 * round(seconds / 10)


def _quota(share_pct: float, population: int, minimum: int) -> int:
    """A share as a count, never below `minimum`: every feature must occur in
    every instance, the 60-client smoke instance included."""
    return min(population, max(minimum, round(share_pct * population / 100)))


def _group_sizes(members: int, rng: random.Random) -> list[int]:
    """Split `members` into groups of two and three, at least one of each."""
    sizes = [2, 3]
    left = members - 5
    while left > 0:
        if left in (2, 3):
            size = left
        elif left == 4:
            size = 2
        elif left == 1:  # cannot happen from 5 + 2s and 3s, but stay total
            sizes[0] += 1
            break
        else:
            size = 3 if rng.random() < 0.3 else 2
        sizes.append(size)
        left -= size
    return sizes


def _place_depots(
    scen: Scenario, geo: _Geography, client_xy: list[tuple[int, int]],
    side: int, rng: random.Random,
) -> list[tuple[int, int]]:
    def jitter(p: tuple[int, int]) -> tuple[int, int]:
        off = max(200, side // 60)
        return (
            _clip(p[0] + rng.randint(-off, off), 0, side),
            _clip(p[1] + rng.randint(-off, off), 0, side),
        )

    rim = [(10, 50), (90, 50), (50, 10), (50, 90), (12, 12)]
    perimeter = [(side * a // 100, side * b // 100) for a, b in rim]
    mode = scen.depot_placement
    if mode == "anchors":
        sites = list(geo.anchors)
    elif mode == "perimeter":
        sites = perimeter
    elif mode == "hub_and_edge":
        sites = [geo.zone_centre] + perimeter
    elif mode == "spread":
        n = len(client_xy)
        cx = sum(p[0] for p in client_xy) // n
        cy = sum(p[1] for p in client_xy) // n
        start = min(range(n), key=lambda i: (_dist2(client_xy[i], (cx, cy)), i))
        sites = []
        for idx in _fps_order(client_xy, first=start):
            sites.append(client_xy[idx])
            if len(sites) == scen.depots:
                break
    else:
        raise GenerationError(f"unknown depot placement {mode!r}")
    if len(sites) < scen.depots:
        raise GenerationError(
            f"{scen.name}: placement {mode!r} offers {len(sites)} sites "
            f"for {scen.depots} depots"
        )
    return [jitter(p) for p in sites[: scen.depots]]


def _build_profiles(scen: Scenario, rng: random.Random) -> list[dict]:
    speed_pct = _clip(60 + scen.area_km, 85, 200)
    profiles = []
    for name in scen.profiles:
        base = PROFILES[name]
        direction = _COMPASS[rng.randrange(len(_COMPASS))]
        strength = scen.asymmetry_permille * int(base["drift_pct"]) // 100
        scale = speed_pct if base["scales_with_area"] else 100
        profiles.append(
            {
                "name": name,
                "detour_permille": int(base["detour_permille"]) + rng.randint(-40, 40),
                "speed_mm_s": int(base["speed_mm_s"]) * scale // 100
                * (95 + rng.randrange(11)) // 100,
                "stop_overhead_s": int(base["stop_overhead_s"]),
                "zone_in_permille": int(base["zone_in_permille"]),
                "zone_out_permille": int(base["zone_out_permille"]),
                "zone_slow_permille": int(base["zone_slow_permille"]),
                "drift_permille": [
                    direction[0] * strength // 1000,
                    direction[1] * strength // 1000,
                ],
                "restricted": bool(base["restricted"]),
            }
        )
    return profiles


# -- the generator ----------------------------------------------------------


def _validate(scen: Scenario) -> None:
    def need(ok: bool, what: str) -> None:
        if not ok:
            raise GenerationError(f"{scen.name}: {what}")

    need(scen.geography in GEOGRAPHIES, f"unknown geography {scen.geography!r}")
    need(scen.tw_regime in TW_REGIMES, f"unknown window regime {scen.tw_regime!r}")
    need(scen.service in SERVICE_KINDS, f"unknown service kind {scen.service!r}")
    need(scen.reload_policy in RELOAD_POLICIES, "unknown reload policy")
    need(scen.overtime_policy in OVERTIME_POLICIES, "unknown overtime policy")
    need(2 <= scen.depots <= 5, "2-5 depots")
    need(scen.load_dims in (2, 3), "2 or 3 load dimensions")
    need(3 <= len(scen.classes) <= 5, "3-5 vehicle classes")
    need("van" in scen.classes, "the van class is mandatory (goes everywhere)")
    need(
        any(VEHICLE_CLASSES[c].profile == "heavy" for c in scen.classes),
        "a heavy class is mandatory (bulky clients)",
    )
    need(scen.profiles[:2] == ("light", "heavy"), "profiles start light, heavy")
    need(len(scen.profiles) <= 3, "at most three profiles")
    need(len(scen.profiles) == 2 or scen.clients <= 1800,
         "a third profile only up to 1,800 clients (RAM)")
    for profile in scen.profiles:
        need(
            any(VEHICLE_CLASSES[c].profile == profile for c in scen.classes
                if c in VEHICLE_CLASSES),
            f"profile {profile!r} is used by no vehicle class (dead matrices)",
        )
    for name in scen.classes:
        need(name in VEHICLE_CLASSES, f"unknown vehicle class {name!r}")
        need(VEHICLE_CLASSES[name].profile in scen.profiles,
             f"class {name!r} needs profile {VEHICLE_CLASSES[name].profile!r}")
    for name in scen.open_route_classes:
        need(name in scen.classes, f"open-route class {name!r} is not in the fleet")
    need(scen.open_route_classes != (), "some class must have open routes")
    need(scen.clients >= 40, "at least 40 clients, or the quotas collide")


def generate(scen: Scenario, which: str = "") -> dict:
    """Build one instance from a scenario row. Pure: same row, same dict."""
    _validate(scen)
    rng = random.Random(scen.seed)
    side = scen.area_km * 1000
    dims = scen.load_dims
    geo = _make_geography(scen.geography, side, rng)
    profiles = _build_profiles(scen, rng)
    profile_index = {p["name"]: i for i, p in enumerate(profiles)}

    # ---- who is where ---------------------------------------------------
    n_members = _quota(scen.group_member_pct, scen.clients, 5)
    if n_members == 6:
        # 6 is the one count that cannot be split into groups of two AND
        # three (2 + 3 = 5, 2 + 2 + 3 = 7); every instance must have both.
        n_members = 7
    group_sizes = _group_sizes(n_members, rng)
    n_plain = scen.clients - n_members

    client_xy: list[tuple[int, int]] = []  # unique client locations
    plain_loc: list[int] = []  # plain client -> index into client_xy
    # `shared_location_pct` is the share of clients that END UP on a location
    # with company. A client that moves in also turns its host into a
    # co-located client, so the chance of moving in is half the target (per
    # mille, to keep 5 % from rounding to 2 %). A first draft used the
    # target itself and put 35 % of t01 into shared buildings, not 20 %.
    for i in range(n_plain):
        if i > 0 and rng.randrange(1000) < 5 * scen.shared_location_pct:
            plain_loc.append(plain_loc[rng.randrange(i)])
        else:
            client_xy.append(geo.draw())
            plain_loc.append(len(client_xy) - 1)

    depot_xy = _place_depots(scen, geo, client_xy, side, rng)
    n_depots = len(depot_xy)
    locations: list[list[int]] = [list(p) for p in depot_xy]
    locations += [list(p) for p in client_xy]

    def new_location(p: tuple[int, int]) -> int:
        locations.append([_clip(p[0], 0, side), _clip(p[1], 0, side)])
        return len(locations) - 1

    # ---- congestion zone and restricted core ----------------------------
    by_centre = sorted(
        range(len(client_xy)),
        key=lambda k: (_dist2(client_xy[k], geo.zone_centre), k),
    )
    zone_edge = client_xy[by_centre[max(1, len(by_centre) * 15 // 100)]]
    zone = {
        "x": geo.zone_centre[0],
        "y": geo.zone_centre[1],
        "radius": math.isqrt(_dist2(zone_edge, geo.zone_centre)),
    }
    n_restricted = _quota(scen.restricted_pct, len(client_xy), 2)
    restricted = sorted(n_depots + k for k in by_centre[:n_restricted])
    restricted_set = set(restricted)

    # ---- service times and windows of the plain clients -----------------
    clients: list[dict] = []
    for i in range(n_plain):
        early, late = _windows(scen.tw_regime, rng)
        clients.append(
            {
                "location": n_depots + plain_loc[i],
                "delivery": [0] * dims,
                "pickup": [0] * dims,
                "service_duration": _service(scen.service, rng),
                "tw_early": early,
                "tw_late": late,
                "release_time": 0,
                "prize": 0,
                "required": True,
                "group": None,
            }
        )

    # ---- demand calibration ---------------------------------------------
    # How many stops could a van make in a shift if only time mattered --
    # no windows, no waiting? A van load is sized to last for
    # `trip_fill_pct` of that, so capacity binds before time does and
    # reloading is a real option everywhere, in a dense city as much as on
    # a 150 km region; and two to three loads fill a shift, so the shift
    # length and overtime are live constraints too. (A first draft sized
    # the load against the window-REDUCED productivity: three loads then
    # filled barely two thirds of a shift and overtime could never pay.)
    light = profiles[profile_index["light"]]
    van = VEHICLE_CLASSES["van"]
    n_locs = len(client_xy)
    spacing_m = math.sqrt(side * side * _DENSITY_PCT[scen.geography] / 100 / n_locs)
    mean_service = sum(c["service_duration"] for c in clients) / n_plain
    nearest_depot_m = [
        min(math.sqrt(_dist2(p, d)) for d in depot_xy) for p in client_xy
    ]
    mean_stem_m = sum(nearest_depot_m) / n_locs

    def stops_per_shift(cls: VehicleClass, windows: bool = True) -> float:
        prof = profiles[profile_index[cls.profile]]
        speed = prof["speed_mm_s"] / 1000
        detour = prof["detour_permille"] / 1000
        leg_s = 0.8 * spacing_m * detour / speed + prof["stop_overhead_s"]
        stem_s = mean_stem_m * detour / speed
        work_s = max(3_600.0, cls.shift_s * 0.9 - 2 * stem_s - 600)
        factor = _TW_FACTOR_PCT[scen.tw_regime] if windows else 100
        return max(3.0, work_s / (mean_service + leg_s)) * factor / 100

    stops_per_trip = _clip(
        round(scen.trip_fill_pct * stops_per_shift(van, windows=False) / 100), 6, 45
    )
    mean_kg = van.capacity[0] * 0.85 / stops_per_trip
    max_kg = mean_kg / 0.2875  # E[_skew] = 0.05 + 0.95 / 4
    cold_max = max(2, round(van.capacity[2] * 0.9 / (0.4 * stops_per_trip) * 2) - 1)
    smallest = min((VEHICLE_CLASSES[c] for c in scen.classes), key=lambda c: c.capacity[0])

    def parcel(limit: tuple[int, ...]) -> list[int]:
        kg = _clip(round(max_kg * _skew(rng)), 1, limit[0])
        dm3 = _clip(round(kg * (5 + 6 * rng.random())), 1, limit[1])
        load = [kg, dm3]
        if dims == 3:
            cold = 1 + rng.randrange(cold_max) if rng.random() < 0.4 else 0
            load.append(min(cold, limit[2]))
        return load

    van_limit = tuple(c * 85 // 100 for c in van.capacity)
    heavy_cls = max(
        (VEHICLE_CLASSES[c] for c in scen.classes if VEHICLE_CLASSES[c].profile == "heavy"),
        key=lambda c: c.capacity[0],
    )

    # ---- what each plain client wants -----------------------------------
    order = list(range(n_plain))
    rng.shuffle(order)
    unrestricted = [i for i in order if clients[i]["location"] not in restricted_set]
    n_bulky = _quota(scen.bulky_pct, n_plain, 2)
    bulky = set(unrestricted[:n_bulky])
    rest = [i for i in order if i not in bulky]
    n_backhaul = _quota(scen.backhaul_pct, n_plain, 2)
    n_pickup = _quota(scen.pickup_pct, n_plain, 2)
    backhaul = set(rest[:n_backhaul])
    pickup = set(rest[n_backhaul : n_backhaul + n_pickup])

    for i in range(n_plain):
        client = clients[i]
        if i in bulky:
            # Too big for a van in weight; needs a heavy vehicle, and takes
            # a quarter of an hour longer to unload.
            kg = round(van.capacity[0] * (1.15 + 1.25 * rng.random()))
            kg = min(kg, heavy_cls.capacity[0] * 65 // 100)
            dm3 = min(round(kg * (5 + 4 * rng.random())), heavy_cls.capacity[1] * 80 // 100)
            client["delivery"] = [kg, dm3] + [0] * (dims - 2)
            client["service_duration"] += 900
        elif i in backhaul:
            client["pickup"] = parcel(van_limit)
        else:
            client["delivery"] = parcel(van_limit)
            if i in pickup:
                client["pickup"] = [
                    max(1, d * (20 + rng.randrange(61)) // 100) if d else 0
                    for d in client["delivery"]
                ]
                client["service_duration"] += 60

    # ---- marginal cost of one more stop: the anchor for every prize ------
    van_per_m = van.unit_distance_cost + van.unit_duration_cost * 1000 / light["speed_mm_s"]
    leg_m = 0.8 * spacing_m * light["detour_permille"] / 1000

    def stop_cost(service_s: int, extra_m: float = 0.0) -> float:
        return (service_s + light["stop_overhead_s"]) * van.unit_duration_cost + (
            2 * leg_m + extra_m
        ) * van_per_m

    def prize_factor() -> float:
        """0.2 .. 3.5, median about 1: some stops are worth it, some not."""
        u = rng.random()
        return 0.2 + 3.3 * u * u

    candidates = [i for i in rest if clients[i]["required"]]
    rng.shuffle(candidates)
    for i in candidates[: _quota(scen.optional_pct, n_plain, 4)]:
        clients[i]["required"] = False
        clients[i]["prize"] = max(1, round(stop_cost(clients[i]["service_duration"]) * prize_factor()))

    candidates = [i for i in rest if i not in backhaul]
    rng.shuffle(candidates)
    for i in candidates[: _quota(scen.release_pct, n_plain, 2)]:
        client = clients[i]
        release = rng.choice(RELEASE_WAVES_S)
        client["release_time"] = release
        if client["tw_late"] < release + 5_400:
            # A morning slot for goods that arrive at 11:30 is a promise
            # nobody made: move the slot behind the release, keep its width.
            width = client["tw_late"] - client["tw_early"]
            client["tw_early"] = min(release + 1_800, HORIZON_S - width)
            client["tw_late"] = client["tw_early"] + width

    # ---- mutually exclusive groups --------------------------------------
    groups: list[dict] = []
    n_optional_groups = _quota(scen.optional_group_pct, len(group_sizes), 1)
    n_optional_groups = min(n_optional_groups, len(group_sizes) - 1)
    optional_groups = set(rng.sample(range(len(group_sizes)), n_optional_groups))
    slots = ((7_200, 21_600), (21_600, 36_000), (36_000, HORIZON_S))
    for g, size in enumerate(group_sizes):
        required = g not in optional_groups
        # Two kinds, alternating so both occur in every instance: the same
        # address in alternative delivery slots (members share a location),
        # or home versus one or two parcel lockers nearby.
        kind = "alt_window" if g % 2 == 0 else "alt_address"
        home = geo.draw()
        load = parcel(tuple(c * 85 // 100 for c in smallest.capacity))
        service = _service(scen.service, rng)
        factor = prize_factor()
        members = []
        home_loc = new_location(home)
        offered = rng.sample(slots, size) if kind == "alt_window" else []
        for m in range(size):
            if kind == "alt_window":
                loc, (early, late), svc = home_loc, offered[m], service
            elif m == 0:
                loc, (early, late), svc = home_loc, _windows(scen.tw_regime, rng), service
            else:
                off = max(300, side // 40)
                loc = new_location(
                    (home[0] + rng.randint(-off, off), home[1] + rng.randint(-off, off))
                )
                early, late, svc = 0, HORIZON_S, max(60, service // 2)
            prize = 0
            if not required:
                prize = max(1, round(stop_cost(svc) * factor * (100 if m == 0 else 80) / 100))
            members.append(len(clients))
            clients.append(
                {
                    "location": loc,
                    "delivery": list(load),
                    "pickup": [0] * dims,
                    "service_duration": svc,
                    "tw_early": early,
                    "tw_late": late,
                    "release_time": 0,
                    "prize": prize,
                    "required": False,  # PyVRP: group members are optional,
                    "group": g,         # the GROUP is what is required.
                }
            )
        groups.append({"clients": members, "required": required, "kind": kind})

    # ---- shipments: pick up at A, deliver at B, same route --------------
    shipments: list[dict] = []
    # Integer ceiling, not `round`: 5 per mille of 2,900 clients is 14.5, and
    # round-half-even made that 14 -- 0.48 %, under the 0.5 % the scenario
    # asked for. Rounding up can never undershoot the requested share.
    n_shipments = max(4, -(-scen.shipment_permille * scen.clients // 1000))
    n_optional_shipments = min(
        n_shipments - 1, _quota(scen.optional_shipment_pct, n_shipments, 1)
    )
    optional_shipments = set(rng.sample(range(n_shipments), n_optional_shipments))
    reach = side * 20 // 100
    small = tuple(c * 50 // 100 for c in smallest.capacity)
    for s in range(n_shipments):
        if rng.random() < 0.4:  # collected where a client also lives
            a_loc = clients[rng.randrange(n_plain)]["location"]
        else:
            a_loc = new_location(geo.draw())
        a_xy = tuple(locations[a_loc])
        for _ in range(50):
            b_xy = geo.draw()
            if 0 < _dist2(a_xy, b_xy) <= reach * reach:
                break
        else:
            b_xy = (a_xy[0] + rng.randint(300, reach // 2), a_xy[1] - rng.randint(300, reach // 2))
        b_loc = new_location(b_xy)
        if scen.tw_regime == "loose":
            p_early, p_late, d_early, d_late = 0, HORIZON_S - 7_200, 3_600, HORIZON_S
        else:
            p_early = 7_200 + 3_600 * rng.randrange(5)
            p_late = p_early + rng.choice((7_200, 10_800, 14_400))
            d_early = p_early + 3_600
            d_late = min(HORIZON_S, p_late + rng.choice((7_200, 14_400, 21_600)))
        svc_p, svc_d = _service("short", rng), _service("short", rng)
        prize = 0
        if s in optional_shipments:
            crow = math.sqrt(_dist2(a_xy, tuple(locations[b_loc])))
            extra = crow * light["detour_permille"] / 1000
            prize = max(1, round(stop_cost(svc_p + svc_d, extra) * prize_factor()))
        shipments.append(
            {
                "pickup_location": a_loc,
                "delivery_location": b_loc,
                "pickup_tw_early": p_early,
                "pickup_tw_late": p_late,
                "pickup_service_duration": svc_p,
                "delivery_tw_early": d_early,
                "delivery_tw_late": d_late,
                "delivery_service_duration": svc_d,
                "amount": parcel(small),
                "prize": prize,
                "required": s not in optional_shipments,
            }
        )

    # ---- depots ----------------------------------------------------------
    close = 54_000 if scen.area_km <= 70 else 57_600
    depots = []
    for d in range(n_depots):
        depots.append(
            {
                "location": d,
                # Depot 0 opens at 06:00 and closes last; depot 1 always
                # opens later, which is why shift windows differ per depot.
                "tw_early": 0 if d == 0 else rng.choice((1_800, 3_600)) if d == 1
                else rng.choice((0, 0, 1_800)),
                "tw_late": close if d == 0 else close - rng.choice((0, 0, 1_800)),
                "service_duration": 60 * rng.randint(5, 15),
                "name": f"D{d}",
            }
        )

    inst = {
        "format": FORMAT,
        "name": scen.name,
        "locations": locations,
        "zone": zone,
        "restricted_locations": restricted,
        "profiles": profiles,
        "depots": depots,
        "clients": clients,
        "groups": groups,
        "shipments": shipments,
        "vehicle_types": [],
    }
    travel = _Travel(inst)

    # ---- the fleet -------------------------------------------------------
    has_evening = any(VEHICLE_CLASSES[c].evening for c in scen.classes)
    nearest_other = [
        min((e for e in range(n_depots) if e != d),
            key=lambda e: (_dist2(depot_xy[d], depot_xy[e]), e))
        for d in range(n_depots)
    ]
    # Every location must stay reachable from its nearest depot by every
    # general-purpose class: floor `max_distance` at 1.25x the worst such
    # round trip. Cargo bikes are exempt -- a bike that cannot reach the
    # far side of town is the point of a bike.
    worst_round_trip = []
    for p, profile in enumerate(profiles):
        worst = 0
        for loc in range(n_depots, len(locations)):
            if profile["restricted"] and loc in restricted_set:
                continue
            best = min(
                travel(d, loc, p)[0] + travel(loc, d, p)[0] for d in range(n_depots)
            )
            worst = max(worst, best)
        worst_round_trip.append(worst)

    visits_at = [0] * n_depots  # stops whose nearest depot is d
    evening_only_at = [0] * n_depots
    bulky_at = [0] * n_depots
    light_p = profile_index["light"]
    visit_clients = list(range(n_plain)) + [g["clients"][0] for g in groups]
    for i in visit_clients:
        client = clients[i]
        loc = client["location"]
        d = min(range(n_depots), key=lambda e: (_dist2(tuple(locations[loc]), depot_xy[e]), e))
        visits_at[d] += 1
        bulky_at[d] += int(i in bulky)
        back = travel(loc, d, light_p)[1]
        if has_evening and client["tw_early"] + client["service_duration"] + back + MARGIN_S > DAY_SHIFT_END_S:
            evening_only_at[d] += 1
    for shipment in shipments:
        loc = shipment["pickup_location"]
        d = min(range(n_depots), key=lambda e: (_dist2(tuple(locations[loc]), depot_xy[e]), e))
        visits_at[d] += 2

    mix_total = sum(VEHICLE_CLASSES[c].mix for c in scen.classes)
    overtime_scale = {"standard": (100, 50), "generous": (200, 25), "strict": (50, 100)}
    ot_pct, premium_pct = overtime_scale[scen.overtime_policy]
    vehicle_types: list[dict] = []

    def add_type(name: str, cls_name: str, count: int, start: int, end: int,
                 initial_load: list[int] | None = None, fixed_pct: int = 100) -> None:
        cls = VEHICLE_CLASSES[cls_name]
        p = profile_index[cls.profile]
        prof = profiles[p]
        tw_early = max(cls.shift_start_s, depots[start]["tw_early"])
        tw_late = depots[end]["tw_late"]
        if has_evening and not cls.evening:
            tw_late = min(tw_late, DAY_SHIFT_END_S)
        max_distance = cls.max_distance_km * 1000
        shift = cls.shift_s
        if cls_name != "cargo_bike":
            max_distance = max_distance * math.isqrt(scen.area_km * 10_000 // 40) // 100
            max_distance = max(max_distance, worst_round_trip[p] * 125 // 100)
            worst_s = round(worst_round_trip[p] * 1000 / prof["speed_mm_s"] * 1.5)
            shift = max(shift, worst_s + 5_400)
        if scen.reload_policy == "own":
            reloads = {start, end}
        elif scen.reload_policy == "nearest_two":
            reloads = {start, end, nearest_other[start]}
        else:
            reloads = set(range(n_depots))
        if initial_load:
            # A vehicle with yesterday's returns on board may always drop
            # them at the neighbouring depot too. This is also what makes
            # "several reload depots" a feature of EVERY instance: under the
            # `own` policy it otherwise exists only if an open-route class
            # happens to have reloads, and t02 and t08 had none.
            reloads.add(nearest_other[start])
        overtime = cls.overtime_s * ot_pct // 100
        if scen.overtime_policy == "strict" and cls_name != "van":
            overtime = 0
        vehicle_types.append(
            {
                "name": name,
                "class": cls_name,
                "num_available": count,
                "capacity": list(cls.capacity[:dims]),
                "start_depot": start,
                "end_depot": end,
                "fixed_cost": cls.fixed_cost * scen.fixed_cost_pct // 100 * fixed_pct // 100,
                "tw_early": tw_early,
                "tw_late": tw_late,
                "shift_duration": shift,
                "max_distance": max_distance,
                "unit_distance_cost": cls.unit_distance_cost,
                "unit_duration_cost": cls.unit_duration_cost,
                "profile": p,
                "start_late": min(tw_early + cls.start_window_s, depots[start]["tw_late"]),
                "initial_load": initial_load or [0] * dims,
                "reload_depots": sorted(reloads) if cls.max_reloads else [],
                "max_reloads": cls.max_reloads,
                "max_overtime": overtime,
                "unit_overtime_cost": cls.unit_duration_cost * premium_pct // 100 if overtime else 0,
            }
        )

    for d in range(n_depots):
        for cls_name in scen.classes:
            cls = VEHICLE_CLASSES[cls_name]
            per_vehicle = min(
                stops_per_shift(cls),
                cls.capacity[0] * 0.85 / mean_kg * (1 + cls.max_reloads),
            )
            share = visits_at[d] * cls.mix / mix_total
            count = math.ceil(scen.fleet_tightness_pct / 100 * share / per_vehicle) + 1
            if cls.evening:
                count = max(count, math.ceil(
                    scen.fleet_tightness_pct / 100 * evening_only_at[d] / per_vehicle) + 1)
            if cls.profile == "heavy":
                count = max(count, math.ceil(bulky_at[d] / 6) + 1)
            n_open = count // 3 if cls_name in scen.open_route_classes else 0
            add_type(f"{cls_name}@D{d}", cls_name, count - n_open, d, d)
            if n_open:
                e = nearest_other[d]
                add_type(f"{cls_name}@D{d}>D{e}", cls_name, n_open, d, e)

    # Vehicles that start the day with yesterday's returns still on board:
    # `initial_load` occupies capacity until the first depot visit, so they
    # are cheaper to take but start with a smaller first trip.
    preloaded: dict[tuple[str, int], int] = {}
    for k in range(scen.preloaded_vehicles):
        key = ("van" if k % 2 == 0 else "box_truck", k % n_depots)
        if key[0] not in scen.classes:
            key = ("van", key[1])
        preloaded[key] = preloaded.get(key, 0) + 1
    for (cls_name, d), count in sorted(preloaded.items()):
        cap = VEHICLE_CLASSES[cls_name].capacity[:dims]
        load = [c * (25 + rng.randrange(21)) // 100 for c in cap]
        add_type(f"{cls_name}-preloaded@D{d}", cls_name, count, d, d,
                 initial_load=load, fixed_pct=70)

    inst["vehicle_types"] = vehicle_types

    # ---- feasibility by construction ------------------------------------
    plain_types = [vt for vt in vehicle_types if not any(vt["initial_load"])]
    repaired = 0
    for client in clients:
        repaired += _repair_client(client, plain_types, depots, travel, scen.name)
    for shipment in shipments:
        repaired += _repair_shipment(shipment, plain_types, depots, travel, scen.name)

    inst["meta"] = {
        "set": which,
        "seed": scen.seed,
        "scenario": _scenario_dict(scen),
        "units": {
            "distance": "metre", "duration": "second", "time_zero": "06:00",
            "load": ["kilogram", "cubic decimetre", "cold box"][:dims],
            "cost": "1e-4 currency units",
        },
        "forbidden": FORBIDDEN,
        "calibration": {
            "van_stops_per_trip": stops_per_trip,
            "mean_parcel_kg": round(mean_kg),
            "repaired_stops": repaired,
        },
    }
    return inst


def _scenario_dict(scen: Scenario) -> dict:
    return {k: list(v) if isinstance(v, tuple) else v for k, v in asdict(scen).items()}


def _singleton_failure(
    loc: int, early: int, late: int, service: int, release: int,
    demand: list[int], vt: dict, depots: list[dict], travel: _Travel,
) -> tuple[str, int] | None:
    """Why `vt` cannot serve this one stop on a route of its own, or None.

    Deliberately conservative: no overtime, a `MARGIN_S` cushion on every
    time check, and a released stop only counts if the vehicle can still
    START after the release (the real solver may also put it on a later trip
    of a vehicle that started earlier). Returns (reason, value); the value
    is the earliest arrival for `late` and the latest service start that
    still gets home for `return`.
    """
    if any(d > c for d, c in zip(demand, vt["capacity"])):
        return "capacity", 0
    start, end, p = vt["start_depot"], vt["end_depot"], vt["profile"]
    out_d, out_t = travel(start, loc, p)
    back_d, back_t = travel(loc, end, p)
    if out_d >= FORBIDDEN or back_d >= FORBIDDEN:
        return "access", 0
    if out_d + back_d > vt["max_distance"]:
        return "distance", 0
    depart = max(vt["tw_early"], depots[start]["tw_early"], release)
    latest_depart = min(vt["start_late"], depots[start]["tw_late"])
    if depart > latest_depart:
        return "start", latest_depart
    load_s = depots[start]["service_duration"]
    arrive = depart + load_s + out_t
    if arrive + MARGIN_S > late:
        return "late", arrive
    begin = max(arrive, early)
    home_by = min(vt["tw_late"], depots[end]["tw_late"])
    if begin + service + back_t + MARGIN_S > home_by:
        return "return", home_by - back_t - service - MARGIN_S
    wait = max(0, early - (latest_depart + load_s + out_t))
    if load_s + out_t + wait + service + back_t + MARGIN_S > vt["shift_duration"]:
        return "shift", 0
    return None


def _repair_client(
    client: dict, types: list[dict], depots: list[dict], travel: _Travel, name: str
) -> int:
    """Make sure some vehicle type can serve `client` alone. Returns 1 if the
    client had to be changed. Deterministic: nothing is re-drawn."""
    demand = [max(d, p) for d, p in zip(client["delivery"], client["pickup"])]
    changed = 0
    for _ in range(4):
        failures = []
        for vt in types:
            why = _singleton_failure(
                client["location"], client["tw_early"], client["tw_late"],
                client["service_duration"], client["release_time"], demand,
                vt, depots, travel,
            )
            if why is None:
                return changed
            failures.append(why)
        timing = [f for f in failures if f[0] in ("start", "late", "return", "shift")]
        if not timing:
            raise GenerationError(
                f"{name}: no vehicle type can carry or reach a client at "
                f"location {client['location']} with demand {demand}"
            )
        changed = 1
        width = client["tw_late"] - client["tw_early"]
        if client["release_time"] and any(f[0] == "start" for f in timing):
            client["release_time"] = 0
        elif any(f[0] == "late" for f in timing):
            arrive = min(f[1] for f in timing if f[0] == "late")
            client["tw_late"] = min(HORIZON_S, max(client["tw_late"], arrive + 2 * MARGIN_S))
            client["tw_early"] = min(client["tw_early"], client["tw_late"] - min(width, 3_600))
        elif any(f[0] == "return" for f in timing):
            begin = max(f[1] for f in timing if f[0] == "return")
            client["tw_early"] = max(0, min(client["tw_early"], begin - MARGIN_S))
            client["tw_late"] = max(client["tw_late"], client["tw_early"] + min(width, 3_600))
        else:
            client["tw_early"], client["tw_late"], client["release_time"] = 0, HORIZON_S, 0
    client["tw_early"], client["tw_late"], client["release_time"] = 0, HORIZON_S, 0
    for vt in types:
        if _singleton_failure(
            client["location"], 0, HORIZON_S, client["service_duration"], 0,
            demand, vt, depots, travel,
        ) is None:
            return 1
    raise GenerationError(
        f"{name}: a client at location {client['location']} is unreachable "
        "even with an all-day window"
    )


def _shipment_ok(shipment: dict, vt: dict, depots: list[dict], travel: _Travel) -> bool:
    """Depot -> pickup -> delivery -> depot on a route of its own."""
    if any(a > c for a, c in zip(shipment["amount"], vt["capacity"])):
        return False
    start, end, p = vt["start_depot"], vt["end_depot"], vt["profile"]
    a, b = shipment["pickup_location"], shipment["delivery_location"]
    legs = [travel(start, a, p), travel(a, b, p), travel(b, end, p)]
    if any(d >= FORBIDDEN for d, _ in legs):
        return False
    if sum(d for d, _ in legs) > vt["max_distance"]:
        return False
    depart = max(vt["tw_early"], depots[start]["tw_early"])
    if depart > min(vt["start_late"], depots[start]["tw_late"]):
        return False
    load_s = depots[start]["service_duration"]
    at_a = depart + load_s + legs[0][1]
    if at_a + MARGIN_S > shipment["pickup_tw_late"]:
        return False
    leave_a = max(at_a, shipment["pickup_tw_early"]) + shipment["pickup_service_duration"]
    at_b = leave_a + legs[1][1]
    if at_b + MARGIN_S > shipment["delivery_tw_late"]:
        return False
    leave_b = max(at_b, shipment["delivery_tw_early"]) + shipment["delivery_service_duration"]
    if leave_b + legs[2][1] + MARGIN_S > min(vt["tw_late"], depots[end]["tw_late"]):
        return False
    # Worst case for the shift length: start as early as possible.
    return leave_b + legs[2][1] - depart + MARGIN_S <= vt["shift_duration"] or (
        vt["start_late"] > depart
        and leave_b + legs[2][1] - min(vt["start_late"], max(depart, shipment["pickup_tw_early"] - load_s - legs[0][1]))
        + MARGIN_S <= vt["shift_duration"]
    )


def _repair_shipment(
    shipment: dict, types: list[dict], depots: list[dict], travel: _Travel, name: str
) -> int:
    if any(_shipment_ok(shipment, vt, depots, travel) for vt in types):
        return 0
    shipment["pickup_tw_early"], shipment["pickup_tw_late"] = 0, HORIZON_S - 7_200
    shipment["delivery_tw_early"], shipment["delivery_tw_late"] = 0, HORIZON_S
    if any(_shipment_ok(shipment, vt, depots, travel) for vt in types):
        return 1
    raise GenerationError(
        f"{name}: shipment {shipment['pickup_location']} -> "
        f"{shipment['delivery_location']} is infeasible even with all-day windows"
    )


# -- writing ----------------------------------------------------------------


def dumps(inst: dict) -> str:
    """Canonical text of an instance: sorted keys, no spaces, ASCII, one
    record per line for the long lists (diffable), `\n` newlines only."""

    def compact(value: object) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    lines = ["{"]
    keys = sorted(inst)
    for k, key in enumerate(keys):
        value = inst[key]
        comma = "," if k < len(keys) - 1 else ""
        if isinstance(value, list) and value and isinstance(value[0], (dict, list)):
            lines.append(f'"{key}":[')
            lines += [
                compact(row) + ("," if r < len(value) - 1 else "")
                for r, row in enumerate(value)
            ]
            lines.append("]" + comma)
        else:
            lines.append(f'"{key}":{compact(value)}{comma}')
    lines.append("}")
    return "\n".join(lines) + "\n"


def generate_set(which: str) -> list[tuple[str, str]]:
    """(file name, canonical text) for every instance of a set."""
    return [
        (f"{scen.name}.json", dumps(generate(scen, which))) for scen in scenarios(which)
    ]


def _load_sibling(name: str):
    """Import a sibling module by path. `instance`, `solve`, ... are too
    generic to trust `sys.path` with: whatever else is importable under that
    name must not win."""
    key = f"pyvrp_hard_{name}"
    if key in sys.modules:
        return sys.modules[key]
    spec = importlib.util.spec_from_file_location(key, HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    return module


def manifest_entry(which: str, scen: Scenario, inst: dict, text: str) -> dict:
    inventory = _load_sibling("instance").feature_inventory(inst)
    payload = text.encode("ascii")
    return {
        "set": which,
        "file": f"{scen.name}.json",
        "seed": scen.seed,
        "scenario": _scenario_dict(scen),
        "clients": len(inst["clients"]),
        "locations": len(inst["locations"]),
        "depots": len(inst["depots"]),
        "groups": len(inst["groups"]),
        "shipments": len(inst["shipments"]),
        "vehicle_types": len(inst["vehicle_types"]),
        "vehicles": sum(vt["num_available"] for vt in inst["vehicle_types"]),
        "calibration": inst["meta"]["calibration"],
        "features": inventory,
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


REGENERATE = {
    "tuning": "python generate.py --set tuning --out instances/",
    "fresh": "python generate.py --set fresh --out instances/",
    "smoke": "python generate.py --set smoke --out smoke/",
}


def write_set(which: str, out_dir: Path, manifest_path: Path | None) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for scen in scenarios(which):
        inst = generate(scen, which)
        text = dumps(inst)
        # Binary write: text mode on Windows would turn "\n" into "\r\n".
        (out_dir / f"{scen.name}.json").write_bytes(text.encode("ascii"))
        entries.append(manifest_entry(which, scen, inst, text))
    if manifest_path is not None:
        manifest = {"format": FORMAT, "regenerate": REGENERATE, "instances": {}}
        if manifest_path.exists():
            old = json.loads(manifest_path.read_text(encoding="utf-8"))
            if old.get("format") == FORMAT:
                manifest["instances"] = old.get("instances", {})
        manifest["instances"] = {
            name: entry
            for name, entry in manifest["instances"].items()
            if entry["set"] != which
        }
        for entry in entries:
            manifest["instances"][entry["file"][: -len(".json")]] = entry
        text = json.dumps(manifest, sort_keys=True, indent=1, ensure_ascii=True) + "\n"
        manifest_path.write_bytes(text.encode("ascii"))
    return entries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--set", required=True, choices=("tuning", "fresh", "smoke"),
                        dest="which", help="which instance set to write")
    parser.add_argument("--out", required=True, type=Path, help="output directory")
    parser.add_argument("--manifest", type=Path, default=HERE / "manifest.json",
                        help="manifest to update (default: next to this script)")
    parser.add_argument("--no-manifest", action="store_true",
                        help="write the instances only")
    args = parser.parse_args(argv)
    entries = write_set(args.which, args.out, None if args.no_manifest else args.manifest)
    for entry in entries:
        print(f"{entry['file']}: {entry['clients']} clients, {entry['locations']} "
              f"locations, {entry['vehicles']} vehicles in {entry['vehicle_types']} "
              f"types, {entry['bytes']:,} bytes, sha256 {entry['sha256'][:12]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
