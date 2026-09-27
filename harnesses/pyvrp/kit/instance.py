"""Load a `pyvrp_hard` instance and turn it into a `pyvrp.ProblemData`.

Why this is a module of its own
-------------------------------
The instance files hold coordinates and attributes only. The travel matrices
are a closed-form function of them (see `generate.edge`, the normative scalar
version) and are rebuilt here, vectorised, every time an instance is loaded:
a 3,000-location int64 matrix is 72 MB, an instance needs four to six of
them, and none of that belongs in a JSON file or in git.

Three consumers share this module: `solve.py` (the solver front end),
`generate.py` (which only wants `feature_inventory` for the manifest) and the
tests. numpy and pyvrp are therefore imported lazily, inside the functions
that need them -- the generator and the inventory run on a bare Python.

Bit-exactness
-------------
`build_matrices` uses the same operations in the same order as
`generate.edge`: integer differences, `sqrt`, `+ - * /` on float64 and
round-half-even. IEEE 754 requires every one of them to be correctly rounded,
and numpy evaluates each ufunc separately (no fused multiply-add), so the
matrices -- and hence the objective values -- are identical on every machine.
`tests/test_pyvrp_hard_benchmark.py` cross-checks the two implementations.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    import numpy as np
    import pyvrp

__all__ = [
    "FEATURE_MINIMUMS",
    "FORMAT",
    "HORIZON_S",
    "build_matrices",
    "build_problem_data",
    "feature_inventory",
    "infeasibility_pricing",
    "load_instance",
    "missing_features",
    "unservable_stops",
]

FORMAT = "pyvrp-hard/1"
HORIZON_S = 50_400

_BLOCK_ROWS = 512
"""Rows per block when filling a matrix. Bounds the float64 temporaries to a
few tens of megabytes instead of five full L x L arrays."""


def load_instance(path: str | Path) -> dict:
    """Read an instance file. Raises `ValueError` on anything that is not a
    `pyvrp-hard/1` instance, so a wrong path fails here and not in PyVRP."""
    with open(path, "r", encoding="utf-8") as handle:
        instance = json.load(handle)
    if not isinstance(instance, dict) or instance.get("format") != FORMAT:
        raise ValueError(f"{path}: not a {FORMAT} instance file")
    for key in ("locations", "depots", "clients", "vehicle_types", "profiles",
                "groups", "shipments", "zone", "restricted_locations"):
        if key not in instance:
            raise ValueError(f"{path}: instance is missing {key!r}")
    return instance


def build_matrices(instance: dict) -> tuple[list["np.ndarray"], list["np.ndarray"]]:
    """Distance and duration matrices, one pair per routing profile.

    int64, indexed by location, asymmetric, zero diagonal. Rows and columns
    of restricted locations are set to the instance's `forbidden` value for
    profiles that may not go there.
    """
    import numpy as np

    xy = np.asarray(instance["locations"], dtype=np.int64)
    x, y = xy[:, 0].copy(), xy[:, 1].copy()
    n = len(x)
    zone = instance["zone"]
    in_zone = (x - zone["x"]) ** 2 + (y - zone["y"]) ** 2 <= zone["radius"] ** 2
    in_zone_i = in_zone.astype(np.int64)
    restricted = np.asarray(instance["restricted_locations"], dtype=np.int64)
    forbidden = int(instance["meta"]["forbidden"])

    distances, durations = [], []
    for profile in instance["profiles"]:
        drift_x, drift_y = profile["drift_permille"]
        detour = profile["detour_permille"] / 1000.0
        speed = float(profile["speed_mm_s"])
        dist = np.empty((n, n), dtype=np.int64)
        time = np.empty((n, n), dtype=np.int64)
        for lo in range(0, n, _BLOCK_ROWS):
            hi = min(n, lo + _BLOCK_ROWS)
            dx = x[None, :] - x[lo:hi, None]
            dy = y[None, :] - y[lo:hi, None]
            e = np.sqrt((dx * dx + dy * dy).astype(np.float64))
            along = (dx * drift_x + dy * drift_y).astype(np.float64) / 1000.0
            zone_extra = (
                profile["zone_in_permille"] * in_zone_i[None, :]
                + profile["zone_out_permille"] * in_zone_i[lo:hi, None]
            ).astype(np.float64)
            block = np.rint((e + along) * detour * (1.0 + zone_extra / 1000.0))
            touch = (in_zone[None, :] | in_zone[lo:hi, None]).astype(np.float64)
            slow = 1.0 + (profile["zone_slow_permille"] * touch) / 1000.0
            dist[lo:hi] = block.astype(np.int64)
            time[lo:hi] = (
                np.rint(block * 1000.0 / speed * slow).astype(np.int64)
                + profile["stop_overhead_s"]
            )
            del dx, dy, e, along, zone_extra, block, touch, slow
        if profile["restricted"] and len(restricted):
            for matrix in (dist, time):
                matrix[restricted, :] = forbidden
                matrix[:, restricted] = forbidden
        np.fill_diagonal(dist, 0)
        np.fill_diagonal(time, 0)
        distances.append(dist)
        durations.append(time)
    return distances, durations


def build_problem_data(instance: dict) -> "pyvrp.ProblemData":
    """The `pyvrp.ProblemData` of an instance. The numpy matrices are dropped
    before returning; PyVRP keeps its own copy."""
    from pyvrp import (
        Client,
        ClientGroup,
        Depot,
        Location,
        ProblemData,
        Shipment,
        VehicleType,
    )

    n_depots = len(instance["depots"])
    locations = [
        Location(x, y, name=f"D{i}" if i < n_depots else "")
        for i, (x, y) in enumerate(instance["locations"])
    ]
    depots = [
        Depot(
            location=d["location"], tw_early=d["tw_early"], tw_late=d["tw_late"],
            service_duration=d["service_duration"], name=d["name"],
        )
        for d in instance["depots"]
    ]
    clients = [
        Client(
            location=c["location"], delivery=c["delivery"], pickup=c["pickup"],
            service_duration=c["service_duration"], tw_early=c["tw_early"],
            tw_late=c["tw_late"], release_time=c["release_time"],
            prize=c["prize"], required=c["required"], group=c["group"],
            name=f"c{i}",
        )
        for i, c in enumerate(instance["clients"])
    ]
    groups = [
        ClientGroup(clients=g["clients"], required=g["required"], name=f"g{i}")
        for i, g in enumerate(instance["groups"])
    ]
    shipments = [
        Shipment(
            pickup_location=s["pickup_location"],
            delivery_location=s["delivery_location"],
            pickup_tw_early=s["pickup_tw_early"],
            pickup_tw_late=s["pickup_tw_late"],
            pickup_service_duration=s["pickup_service_duration"],
            delivery_tw_early=s["delivery_tw_early"],
            delivery_tw_late=s["delivery_tw_late"],
            delivery_service_duration=s["delivery_service_duration"],
            amount=s["amount"], prize=s["prize"], required=s["required"],
            name=f"s{i}",
        )
        for i, s in enumerate(instance["shipments"])
    ]
    vehicle_types = [
        VehicleType(**{k: v for k, v in vt.items() if k != "class"})
        for vt in instance["vehicle_types"]
    ]
    distances, durations = build_matrices(instance)
    data = ProblemData(
        locations=locations, clients=clients, depots=depots,
        vehicle_types=vehicle_types, distance_matrices=distances,
        duration_matrices=durations, groups=groups, shipments=shipments,
    )
    del distances, durations
    return data


# -- what is in an instance -------------------------------------------------

FEATURE_MINIMUMS: dict[str, int] = {
    # Structure.
    "depots": 2,
    "profiles": 2,
    "load_dimensions": 2,
    "vehicle_classes": 3,
    "distinct_depot_time_windows": 2,
    "distinct_shift_windows": 2,
    # Locations and matrices.
    "clients_on_shared_locations": 1,
    "shipment_stops_on_client_locations": 1,
    "asymmetric_profiles": 1,
    "profiles_with_access_restrictions": 1,
    "restricted_locations": 1,
    "congestion_zone_locations": 1,
    # Clients.
    "clients_delivery_only": 1,
    "clients_pickup_and_delivery": 1,
    "clients_pickup_only": 1,
    "clients_with_time_window": 1,
    "clients_with_service_duration": 1,
    "clients_with_release_time": 1,
    "clients_optional_with_prize": 1,
    "clients_too_big_for_some_vehicle": 1,
    # Groups and shipments.
    "groups_required": 1,
    "groups_optional": 1,
    "groups_of_two": 1,
    "groups_of_three": 1,
    "groups_sharing_a_location": 1,
    "shipments_required": 1,
    "shipments_optional": 1,
    "shipments_with_time_windows": 1,
    # Depots and fleet.
    "depots_with_service_duration": 1,
    "vehicle_types_with_fixed_cost": 1,
    "vehicle_types_with_distance_cost": 1,
    "vehicle_types_with_duration_cost": 1,
    "vehicle_types_with_overtime": 1,
    "vehicle_types_with_reloads": 1,
    "vehicle_types_with_several_reload_depots": 1,
    "vehicle_types_with_open_route": 1,
    "vehicle_types_with_initial_load": 1,
    "vehicle_types_with_start_late": 1,
    "vehicle_types_with_shift_window": 1,
    "vehicle_types_with_shift_duration": 1,
    "vehicle_types_with_max_distance": 1,
}
"""Every modelling feature of PyVRP 0.14.0, as a key of `feature_inventory`
and the least count at which the feature counts as used. Every instance of
every set must meet every line; the tests and `verify.py` enforce it."""


def feature_inventory(instance: dict) -> dict[str, int]:
    """How often each modelling feature occurs in `instance`. Pure Python on
    the JSON dict; no solver needed."""
    clients = instance["clients"]
    groups = instance["groups"]
    shipments = instance["shipments"]
    depots = instance["depots"]
    types = instance["vehicle_types"]
    profiles = instance["profiles"]
    dims = len(types[0]["capacity"])

    per_location: dict[int, int] = {}
    for c in clients:
        per_location[c["location"]] = per_location.get(c["location"], 0) + 1
    zone = instance["zone"]
    r2 = zone["radius"] ** 2
    in_zone = sum(
        (x - zone["x"]) ** 2 + (y - zone["y"]) ** 2 <= r2
        for x, y in instance["locations"]
    )

    def plain(c: dict) -> bool:
        return c["group"] is None

    def too_big(c: dict) -> bool:
        need = [max(d, p) for d, p in zip(c["delivery"], c["pickup"])]
        return any(any(n > cap for n, cap in zip(need, vt["capacity"])) for vt in types)

    unbounded = 1 << 60
    inventory = {
        "clients": len(clients),
        "locations": len(instance["locations"]),
        "depots": len(depots),
        "profiles": len(profiles),
        "load_dimensions": dims,
        "load_dimensions_in_use": sum(
            any(c["delivery"][d] or c["pickup"][d] for c in clients) for d in range(dims)
        ),
        "groups": len(groups),
        "group_members": sum(len(g["clients"]) for g in groups),
        "shipments": len(shipments),
        "vehicle_types": len(types),
        "vehicle_classes": len({vt["class"] for vt in types}),
        "vehicles": sum(vt["num_available"] for vt in types),
        "distinct_depot_time_windows": len({(d["tw_early"], d["tw_late"]) for d in depots}),
        "distinct_shift_windows": len(
            {(vt["tw_early"], vt["start_late"], vt["tw_late"]) for vt in types}
        ),
        "clients_on_shared_locations": sum(
            per_location[c["location"]] > 1 for c in clients
        ),
        "shipment_stops_on_client_locations": sum(
            (s["pickup_location"] in per_location) + (s["delivery_location"] in per_location)
            for s in shipments
        ),
        "asymmetric_profiles": sum(
            any(p["drift_permille"]) or p["zone_in_permille"] != p["zone_out_permille"]
            for p in profiles
        ),
        "profiles_with_access_restrictions": sum(bool(p["restricted"]) for p in profiles),
        "restricted_locations": len(instance["restricted_locations"]),
        "congestion_zone_locations": in_zone,
        "clients_delivery_only": sum(
            any(c["delivery"]) and not any(c["pickup"]) for c in clients
        ),
        "clients_pickup_and_delivery": sum(
            any(c["delivery"]) and any(c["pickup"]) for c in clients
        ),
        "clients_pickup_only": sum(
            any(c["pickup"]) and not any(c["delivery"]) for c in clients
        ),
        "clients_with_time_window": sum(
            c["tw_early"] > 0 or c["tw_late"] < HORIZON_S for c in clients
        ),
        "clients_with_tight_window": sum(
            c["tw_late"] - c["tw_early"] <= 7_200 for c in clients
        ),
        "clients_with_service_duration": sum(c["service_duration"] > 0 for c in clients),
        "clients_with_release_time": sum(c["release_time"] > 0 for c in clients),
        "clients_optional_with_prize": sum(
            plain(c) and not c["required"] and c["prize"] > 0 for c in clients
        ),
        "clients_too_big_for_some_vehicle": sum(too_big(c) for c in clients),
        "groups_required": sum(g["required"] for g in groups),
        "groups_optional": sum(not g["required"] for g in groups),
        "groups_of_two": sum(len(g["clients"]) == 2 for g in groups),
        "groups_of_three": sum(len(g["clients"]) == 3 for g in groups),
        "groups_sharing_a_location": sum(
            len({clients[i]["location"] for i in g["clients"]}) == 1 for g in groups
        ),
        "shipments_required": sum(s["required"] for s in shipments),
        "shipments_optional": sum(not s["required"] and s["prize"] > 0 for s in shipments),
        "shipments_with_time_windows": sum(
            s["pickup_tw_early"] > 0 or s["delivery_tw_late"] < HORIZON_S
            or s["pickup_tw_late"] < HORIZON_S for s in shipments
        ),
        "depots_with_service_duration": sum(d["service_duration"] > 0 for d in depots),
        "vehicle_types_with_fixed_cost": sum(vt["fixed_cost"] > 0 for vt in types),
        "vehicle_types_with_distance_cost": sum(vt["unit_distance_cost"] > 0 for vt in types),
        "vehicle_types_with_duration_cost": sum(vt["unit_duration_cost"] > 0 for vt in types),
        "vehicle_types_with_overtime": sum(
            vt["max_overtime"] > 0 and vt["unit_overtime_cost"] > 0 for vt in types
        ),
        "vehicle_types_with_reloads": sum(
            vt["max_reloads"] > 0 and len(vt["reload_depots"]) > 0 for vt in types
        ),
        "vehicle_types_with_several_reload_depots": sum(
            vt["max_reloads"] > 0 and len(vt["reload_depots"]) > 1 for vt in types
        ),
        "vehicle_types_with_open_route": sum(
            vt["start_depot"] != vt["end_depot"] for vt in types
        ),
        "vehicle_types_with_initial_load": sum(any(vt["initial_load"]) for vt in types),
        "vehicle_types_with_start_late": sum(vt["start_late"] < vt["tw_late"] for vt in types),
        "vehicle_types_with_shift_window": sum(
            vt["tw_early"] > 0 or vt["tw_late"] < unbounded for vt in types
        ),
        "vehicle_types_with_shift_duration": sum(
            vt["shift_duration"] < vt["tw_late"] - vt["tw_early"] for vt in types
        ),
        "vehicle_types_with_max_distance": sum(vt["max_distance"] < unbounded for vt in types),
    }
    return {key: int(value) for key, value in inventory.items()}


def missing_features(instance: dict) -> list[str]:
    """Features of `FEATURE_MINIMUMS` the instance does not use. Empty for
    every instance this benchmark ships."""
    inventory = feature_inventory(instance)
    missing = [
        f"{key} = {inventory[key]} < {least}"
        for key, least in FEATURE_MINIMUMS.items()
        if inventory[key] < least
    ]
    if inventory["load_dimensions_in_use"] != inventory["load_dimensions"]:
        missing.append("a load dimension no client uses")
    return missing


# -- pricing an infeasible result -------------------------------------------


def infeasibility_pricing(instance: dict) -> dict:
    """A FIXED price list for constraint violations, derived from the instance.

    `solve.py` must return a finite objective even when the solver found no
    feasible solution, and that number must not depend on the state PyVRP's
    adaptive penalty manager happened to be in. So the violations of the best
    solution are priced with these constants, and `offset` is added on top.

    `offset` is an upper bound on the cost of ANY feasible solution: every
    vehicle on the road for its maximum distance and maximum duration with
    full overtime, and every prize forgone. An infeasible result is therefore
    always worse than any feasible one, and among infeasible results less
    violation is still better -- a tuner keeps a gradient.
    """
    types = instance["vehicle_types"]
    dims = len(types[0]["capacity"])
    dearest = max(vt["fixed_cost"] for vt in types)
    offset = sum(c["prize"] for c in instance["clients"])
    offset += sum(s["prize"] for s in instance["shipments"])
    for vt in types:
        longest = vt["shift_duration"] + vt["max_overtime"]
        offset += vt["num_available"] * (
            vt["fixed_cost"]
            + vt["max_distance"] * vt["unit_distance_cost"]
            + longest * vt["unit_duration_cost"]
            + vt["max_overtime"] * vt["unit_overtime_cost"]
        )
    return {
        # One unit of excess load costs as much as 1 % of the dearest vehicle
        # spread over the smallest capacity in that dimension, times 100.
        "load_penalties": [
            max(1, 100 * dearest // max(1, min(vt["capacity"][d] for vt in types)))
            for d in range(dims)
        ],
        "tw_penalty": 100 * max(vt["unit_duration_cost"] + vt["unit_overtime_cost"] for vt in types),
        "dist_penalty": 100 * max(vt["unit_distance_cost"] for vt in types),
        "missing_penalty": 10 * dearest,
        "offset": offset,
    }


# -- a solver-side check of the generator's feasibility model ---------------


def unservable_stops(data: "pyvrp.ProblemData") -> list[str]:
    """Clients and shipments that NO vehicle type can serve on a route of its
    own, judged by PyVRP itself (`Route.is_feasible`).

    The generator guarantees this list is empty with its own, more
    conservative arithmetic; this is the independent check that the two
    models of a route agree. A client that may only ride on a later trip is
    still servable alone here, because the generator only hands out release
    times that some vehicle type can start after.
    """
    from pyvrp import Activity, ActivityType, Route

    n_types = data.num_vehicle_types
    stuck = []
    for idx in range(data.num_clients):
        visit = [Activity(ActivityType.CLIENT, idx)]
        if not any(Route(data, visit, vt).is_feasible() for vt in range(n_types)):
            stuck.append(f"client {idx}")
    for idx in range(data.num_shipments):
        visit = [
            Activity(ActivityType.PICKUP, idx),
            Activity(ActivityType.DELIVERY, idx),
        ]
        if not any(Route(data, visit, vt).is_feasible() for vt in range(n_types)):
            stuck.append(f"shipment {idx}")
    return stuck
