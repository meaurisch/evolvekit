"""Generate a synthetic-but-realistic metro parcel-delivery VRP instance.

    python make_instance.py --orders 3000 --seed 7 --out instance-3000-s7.json
    python make_instance.py --orders 300  --seed 7 --out instance-300-s7.json

Nothing is downloaded and nothing here is customer data: every number comes
out of `random.Random(seed)`, so the same seed and the same order count give
byte-identical JSON on any machine with any Python 3.11+.

The instance is written as plain JSON rather than as a pickled `ProblemData`
so that it can be read, diffed and version-checked. `skeleton.py` turns it
into a `pyvrp.ProblemData`; the travel matrices are *derived* there from the
coordinates and the profile table in `meta`, because 3,003 x 3,003 int64
matrices are 72 MB each and have no business being in a JSON file.

Units, fixed for the whole instance
-----------------------------------
distance   metres                  (integer)
duration   seconds                 (integer)
load 0     kilograms               (integer)
load 1     cubic decimetres (dm3)  (integer)
cost       1e-4 EUR (a hundredth of a cent)  (integer)
time zero  06:00 local; the horizon runs to 20:00, i.e. 50,400 s.

The cost unit is the one non-obvious choice, and it is chosen from two sides.
PyVRP's `unit_distance_cost` and `unit_duration_cost` are *integers* per metre
and per second, so a van at EUR 0.30/km -- 0.03 cents per metre -- needs a unit
finer than a cent. From the other side, PyVRP's penalty manager clamps its
violation penalties at `PenaltyParams.max_penalty`, 100,000 by default: with
too fine a unit that ceiling is a rounding error next to a vehicle's fixed
cost, the search stops caring about a few seconds of time warp, and PyVRP
raises `PenaltyBoundWarning` to say so. At 1e-4 EUR the ceiling is worth
EUR 10 per second of time warp against a EUR 35 van, which is ample. The
vehicle tariffs are rounded to figures that are exact in this unit.

What the instance models
------------------------
See `README.md` for the full rationale. In brief: a 40 x 40 km metro area with
a dense centre, six suburban clusters and a rural fringe; three depots; parcel
demands in two load dimensions; delivery slots; ~10 % returns collected at the
delivery address; ~5 % optional prize-collecting orders; release times for a
second inbound wave; pairs of alternative addresses as mutually exclusive
client groups; a handful of same-day courier shipments; and a heterogeneous
fleet of vans, small trucks, large trucks and evening vans across two routing
profiles.
"""

from __future__ import annotations

import argparse
import json
import math
import random
from pathlib import Path

__all__ = ["generate", "main", "HORIZON_S", "AREA_M"]

# -- the metro ------------------------------------------------------------

AREA_M = 40_000
"""Side of the square the metro area lives in, in metres. 40 x 40 km."""

CENTRE = (20_000, 20_000)
"""City centre. The dense cluster sits here and so does the main depot."""

CENTRE_RADIUS_M = 6_000
"""Inner-city zone. Heavy vehicles pay a detour factor on edges touching it,
because a 12-tonne truck does not take the same streets a van does."""

SUBURB_COUNT = 6
"""Satellite towns, on a ring around the centre."""

SUBURB_RING_M = (11_000, 16_000)
SUBURB_SIGMA_M = 1_400
CENTRE_SIGMA_M = 2_600
RURAL_MIN_RADIUS_M = 17_000
"""Rural orders are placed outside this radius, so the fringe is really a
fringe rather than more suburb."""

MIX = {"centre": 0.45, "suburb": 0.40, "rural": 0.15}
"""Where the orders are. Sums to 1."""

# -- the day --------------------------------------------------------------

HORIZON_S = 50_400
"""14 hours: 06:00 (t = 0) to 20:00 (t = 50,400). The last delivery slot ends
here; the depots stay open an hour longer so a route that finishes at the far
edge of the evening slot can still get home."""

DEPOT_CLOSE_S = HORIZON_S + 3_600
"""21:00. Vehicle shifts and depot windows end here, not at `HORIZON_S`.

Without the extra hour the last delivery slot and the vehicle's return
deadline are the same instant, so any route that serves a customer at 19:55
lands back at the depot after closing time and is charged time warp for it.
That is not a routing problem, it is a modelling artefact, and it is enough
to make the whole instance infeasible by a minute or two."""


def _clock(hour: float) -> int:
    """Seconds from t = 0 for a 24-hour clock time. `_clock(8) == 7200`."""
    return int(round((hour - 6.0) * 3600.0))


SLOTS = (
    # (name, share, tw_early, tw_late)
    ("morning", 0.25, _clock(8), _clock(12)),
    ("afternoon", 0.25, _clock(12), _clock(16)),
    ("evening", 0.15, _clock(16), _clock(20)),
    ("all_day", 0.25, 0, HORIZON_S),
    ("tight_hour", 0.10, 0, 0),  # start drawn per order; one hour wide
)
"""Delivery slots. `tight_hour` is the awkward one: a single hour somewhere
between 08:00 and 19:00, which is what a customer who took the trouble to
pick a slot actually gets."""

TIGHT_SLOT_WIDTH_S = 3_600

# -- the parcels ----------------------------------------------------------

RETURN_SHARE = 0.10
"""Share of orders that also hand a parcel back: simultaneous pickup and
delivery at the same address, which is what a return label looks like."""

OPTIONAL_SHARE = 0.05
"""Share of orders that are prize-collecting rather than required: a courier
partner will take them if we do not, and the prize is what we save."""

RELEASE_SHARE = 0.08
"""Share of orders that are not on the first trailer of the day."""

GROUP_SHARE = 0.02
"""Share of orders offered at two addresses (home or a nearby parcel shop),
modelled as a mutually exclusive client group of size two. Both members are
optional clients; the group itself is required, so exactly one is visited."""

SHIPMENT_SHARE = 0.012
"""Share of the order count spent on same-day courier jobs: pick up at A,
deliver at B, both on the same route."""

# -- money ----------------------------------------------------------------

EUR = 10_000
"""Cost units per euro. One unit is 1e-4 EUR, a hundredth of a cent."""

# -- profiles -------------------------------------------------------------

ROAD_FACTOR = 1.3
"""Euclidean distance x 1.3 = road distance. The usual detour-index rule of
thumb for a European metro; a straight line is not a street."""

PROFILES = (
    {
        "name": "light",
        "speed_mps": 8.5,  # 30.6 km/h door to door, including parking
        "centre_distance_multiplier": 1.0,
    },
    {
        "name": "heavy",
        "speed_mps": 7.0,  # 25.2 km/h
        "centre_distance_multiplier": 1.10,
    },
)
"""Two routing profiles. Vans use `light`; trucks use `heavy`, which is both
slower and 10 % longer on any edge touching the inner-city zone."""


# --------------------------------------------------------------------------
# geography
# --------------------------------------------------------------------------


def _clamp(value: float, lo: float, hi: float) -> int:
    return int(round(min(hi, max(lo, value))))


def _suburb_centres(rng: random.Random) -> list[tuple[int, int]]:
    """Satellite towns spread around a ring, jittered so they are not regular."""
    centres = []
    for index in range(SUBURB_COUNT):
        angle = 2.0 * math.pi * (index + rng.uniform(-0.18, 0.18)) / SUBURB_COUNT
        radius = rng.uniform(*SUBURB_RING_M)
        centres.append(
            (
                _clamp(CENTRE[0] + radius * math.cos(angle), 0, AREA_M),
                _clamp(CENTRE[1] + radius * math.sin(angle), 0, AREA_M),
            )
        )
    return centres


def _draw_point(rng: random.Random, kind: str, suburbs) -> tuple[int, int]:
    if kind == "centre":
        while True:
            x = rng.gauss(CENTRE[0], CENTRE_SIGMA_M)
            y = rng.gauss(CENTRE[1], CENTRE_SIGMA_M)
            if 0 <= x <= AREA_M and 0 <= y <= AREA_M:
                return int(round(x)), int(round(y))
    if kind == "suburb":
        cx, cy = suburbs[rng.randrange(len(suburbs))]
        return (
            _clamp(rng.gauss(cx, SUBURB_SIGMA_M), 0, AREA_M),
            _clamp(rng.gauss(cy, SUBURB_SIGMA_M), 0, AREA_M),
        )
    # rural: uniform over the square, but genuinely out of town
    for _ in range(200):
        x = rng.uniform(0, AREA_M)
        y = rng.uniform(0, AREA_M)
        if math.hypot(x - CENTRE[0], y - CENTRE[1]) >= RURAL_MIN_RADIUS_M:
            return int(round(x)), int(round(y))
    return AREA_M, AREA_M  # pragma: no cover - the loop above always succeeds


# --------------------------------------------------------------------------
# parcels
# --------------------------------------------------------------------------


def _parcel(rng: random.Random) -> tuple[int, int]:
    """One parcel: (weight kg, volume dm3).

    Weight is lognormal around 3 kg with a long tail -- most of what a parcel
    round carries is small, and the occasional 25 kg box is what breaks the
    weight capacity before the volume one. Volume is weight times a density
    factor, so bulky-but-light freight exists and is what breaks the volume
    capacity instead. Two dimensions that fail at different times is the whole
    reason the instance has two of them.
    """
    weight = min(32, max(1, int(round(rng.lognormvariate(math.log(3.0), 0.9)))))
    volume = min(180, max(2, int(round(weight * rng.uniform(6.0, 14.0)))))
    return weight, volume


def _service_duration(rng: random.Random, weight: int) -> int:
    """2-6 minutes: park, walk, hand over, scan. Heavier takes longer."""
    return _clamp(120 + 5 * weight + rng.uniform(0, 120), 120, 360)


def _slot(rng: random.Random) -> tuple[str, int, int]:
    roll = rng.random()
    cumulative = 0.0
    for name, share, early, late in SLOTS:
        cumulative += share
        if roll < cumulative:
            if name == "tight_hour":
                start = _clock(8) + rng.randrange(0, 11) * 3600
                return name, start, start + TIGHT_SLOT_WIDTH_S
            return name, early, late
    return "all_day", 0, HORIZON_S  # pragma: no cover - shares sum to 1


# --------------------------------------------------------------------------
# the fleet
# --------------------------------------------------------------------------

VEHICLE_CLASSES = (
    {
        "cls": "van",
        "capacity": [900, 7_000],
        "fixed_cost": 35 * EUR,
        "unit_distance_cost": 3,  # EUR 0.30/km
        "unit_duration_cost": 70,  # EUR 25.20/h
        "shift_duration": 9 * 3600,
        "max_distance": 220_000,
        "tw_early": 0,
        "tw_late": DEPOT_CLOSE_S,
        "start_late": _clock(8),
        "profile": 0,
        "reloads": 2,
        "max_overtime": 3_600,
        "unit_overtime_cost": 35,  # +50 % on top of the duration cost
        "per_100_orders": 1.5,
    },
    {
        "cls": "small_truck",
        "capacity": [3_200, 22_000],
        "fixed_cost": 60 * EUR,
        "unit_distance_cost": 5,  # EUR 0.50/km
        "unit_duration_cost": 80,  # EUR 28.80/h
        "shift_duration": int(9.5 * 3600),
        "max_distance": 320_000,
        "tw_early": 0,
        "tw_late": DEPOT_CLOSE_S,
        "start_late": _clock(9),
        "profile": 1,
        "reloads": 1,
        "max_overtime": 1_800,
        "unit_overtime_cost": 40,
        "per_100_orders": 0.6,
    },
    {
        "cls": "large_truck",
        "capacity": [8_000, 48_000],
        "fixed_cost": 95 * EUR,
        "unit_distance_cost": 7,  # EUR 0.70/km
        "unit_duration_cost": 90,  # EUR 32.40/h
        "shift_duration": 10 * 3600,
        "max_distance": 400_000,
        "tw_early": 0,
        "tw_late": DEPOT_CLOSE_S,
        "start_late": _clock(9),
        "profile": 1,
        "reloads": 0,
        "max_overtime": 0,
        "unit_overtime_cost": 0,
        "per_100_orders": 0.3,
    },
    {
        "cls": "evening_van",
        "capacity": [900, 7_000],
        "fixed_cost": 38 * EUR,
        "unit_distance_cost": 3,  # EUR 0.30/km
        "unit_duration_cost": 80,  # EUR 28.80/h, an evening premium on the driver
        "shift_duration": 7 * 3600,
        "max_distance": 180_000,
        "tw_early": _clock(13),
        "tw_late": DEPOT_CLOSE_S,
        "start_late": _clock(15),
        "profile": 0,
        "reloads": 1,
        "max_overtime": 1_800,
        "unit_overtime_cost": 40,
        "per_100_orders": 0.5,
    },
)
"""Four vehicle classes per depot. `per_100_orders` sizes the fleet: it is
deliberately generous, because an instance whose fleet is the binding
constraint measures fleet sizing rather than routing."""

DEPOT_SHIFT_OFFSET_S = (0, 1_800, 0)
"""Depot 1 opens half an hour later than the others: its shift window starts
at 06:30. That is what makes the vehicle time windows per depot differ."""

DEPOT_SERVICE_S = 600
"""Ten minutes to load or unload at a depot, charged on every depot visit --
which, for a vehicle with reload depots, means once per trip."""


# --------------------------------------------------------------------------
# generation
# --------------------------------------------------------------------------


def generate(orders: int, seed: int) -> dict:
    """Build the whole instance as a JSON-ready dict. Deterministic in `seed`."""
    if orders < 10:
        raise ValueError(f"--orders must be at least 10, got {orders}")
    rng = random.Random(seed)
    suburbs = _suburb_centres(rng)

    # -- depots: one central hub plus two of the satellite towns -----------
    depot_points = [
        (CENTRE[0] - 500, CENTRE[1] + 500),
        suburbs[0],
        suburbs[SUBURB_COUNT // 2],
    ]
    depot_names = ("hub-central", "depot-north", "depot-south")

    locations: list[list[int]] = [[x, y] for x, y in depot_points]
    depots = [
        {
            "location": index,
            "tw_early": DEPOT_SHIFT_OFFSET_S[index],
            "tw_late": DEPOT_CLOSE_S,
            "service_duration": DEPOT_SERVICE_S,
            "name": depot_names[index],
        }
        for index in range(len(depot_points))
    ]

    # -- how many of each special kind ------------------------------------
    num_groups = max(1, int(round(orders * GROUP_SHARE / 2)))
    num_shipments = max(1, int(round(orders * SHIPMENT_SHARE)))
    # Group members come out of the order budget: a group of two alternative
    # addresses is one order offered twice, not two orders.
    num_plain = orders - num_groups

    clients: list[dict] = []
    groups: list[dict] = []

    def _add_client(point, **kwargs) -> int:
        locations.append([point[0], point[1]])
        client = {"location": len(locations) - 1}
        client.update(kwargs)
        clients.append(client)
        return len(clients) - 1

    kinds = (["centre"] * round(MIX["centre"] * num_plain)
             + ["suburb"] * round(MIX["suburb"] * num_plain))
    kinds += ["rural"] * (num_plain - len(kinds))
    rng.shuffle(kinds)

    for index, kind in enumerate(kinds):
        point = _draw_point(rng, kind, suburbs)
        weight, volume = _parcel(rng)
        slot_name, tw_early, tw_late = _slot(rng)
        service = _service_duration(rng, weight)

        pickup = [0, 0]
        if rng.random() < RETURN_SHARE:
            back_w, back_v = _parcel(rng)
            pickup = [max(1, back_w // 2), max(1, back_v // 2)]
            service += 60  # scanning a return takes another minute

        release_time = 0
        if rng.random() < RELEASE_SHARE:
            release_time = rng.choice([_clock(9), _clock(10), _clock(11)])
            # A released order cannot also be a morning slot: the goods are
            # not in the building yet. Push it to the rest of the day. The
            # 90-minute margin is deliberate: a one-hour slot that opens the
            # moment the goods are released is unreachable for anything but
            # the depot's own doorstep, and one such order is enough to make
            # the whole instance infeasible.
            if tw_late <= release_time + 5400:
                tw_early, tw_late = max(tw_early, release_time), HORIZON_S
                slot_name = "all_day_released"

        required = True
        prize = 0
        if rng.random() < OPTIONAL_SHARE:
            required = False
            prize = _prize(rng, service)

        clients_kwargs = {
            "delivery": [weight, volume],
            "pickup": pickup,
            "service_duration": service,
            "tw_early": tw_early,
            "tw_late": tw_late,
            "release_time": release_time,
            "prize": prize,
            "required": required,
            "group": None,
            "name": f"order-{index:05d}-{slot_name}",
        }
        _add_client(point, **clients_kwargs)

    # -- alternative addresses: mutually exclusive client groups ----------
    for index in range(num_groups):
        point = _draw_point(rng, "centre" if rng.random() < 0.7 else "suburb", suburbs)
        weight, volume = _parcel(rng)
        service = _service_duration(rng, weight)
        _slot_name, tw_early, tw_late = _slot(rng)
        group_index = len(groups)

        # Home address: the customer's own slot, full service time.
        home = _add_client(
            point,
            delivery=[weight, volume],
            pickup=[0, 0],
            service_duration=service,
            tw_early=tw_early,
            tw_late=tw_late,
            release_time=0,
            prize=0,
            required=False,
            group=group_index,
            name=f"alt-{index:04d}-home",
        )
        # Parcel shop within 800 m: open all day, and quicker to serve because
        # one stop drops many parcels.
        angle = rng.uniform(0, 2 * math.pi)
        radius = rng.uniform(200, 800)
        shop_point = (
            _clamp(point[0] + radius * math.cos(angle), 0, AREA_M),
            _clamp(point[1] + radius * math.sin(angle), 0, AREA_M),
        )
        shop = _add_client(
            shop_point,
            delivery=[weight, volume],
            pickup=[0, 0],
            service_duration=max(120, service // 2),
            tw_early=0,
            tw_late=HORIZON_S,
            release_time=0,
            prize=0,
            required=False,
            group=group_index,
            name=f"alt-{index:04d}-shop",
        )
        groups.append({"clients": [home, shop], "required": True, "name": f"alt-{index:04d}"})

    # -- same-day courier jobs: pick up at A, deliver at B ----------------
    shipments = []
    for index in range(num_shipments):
        pick_point = _draw_point(rng, "centre", suburbs)
        drop_point = _draw_point(rng, "centre" if rng.random() < 0.6 else "suburb", suburbs)
        locations.append([pick_point[0], pick_point[1]])
        pickup_loc = len(locations) - 1
        locations.append([drop_point[0], drop_point[1]])
        delivery_loc = len(locations) - 1
        weight, volume = _parcel(rng)
        ready = rng.choice([_clock(9), _clock(10), _clock(11)])
        shipments.append(
            {
                "pickup_location": pickup_loc,
                "delivery_location": delivery_loc,
                "pickup_tw_early": ready,
                "pickup_tw_late": ready + 3 * 3600,
                "pickup_service_duration": 180,
                "delivery_tw_early": ready,
                "delivery_tw_late": HORIZON_S,
                "delivery_service_duration": 180,
                "amount": [weight, volume],
                "prize": 0,
                "required": True,
                "name": f"courier-{index:04d}",
            }
        )

    # -- the fleet --------------------------------------------------------
    vehicle_types = []
    for depot_index in range(len(depots)):
        offset = DEPOT_SHIFT_OFFSET_S[depot_index]
        for spec in VEHICLE_CLASSES:
            count = max(2, int(round(orders * spec["per_100_orders"] / 100.0)))
            vehicle_types.append(
                {
                    "name": f"{spec['cls']}@{depot_names[depot_index]}",
                    "num_available": count,
                    "capacity": list(spec["capacity"]),
                    "start_depot": depot_index,
                    "end_depot": depot_index,
                    "fixed_cost": spec["fixed_cost"],
                    "tw_early": spec["tw_early"] + offset,
                    "tw_late": spec["tw_late"],
                    "shift_duration": spec["shift_duration"],
                    "max_distance": spec["max_distance"],
                    "unit_distance_cost": spec["unit_distance_cost"],
                    "unit_duration_cost": spec["unit_duration_cost"],
                    "profile": spec["profile"],
                    "start_late": spec["start_late"] + offset,
                    "reload_depots": [depot_index] if spec["reloads"] else [],
                    "max_reloads": spec["reloads"],
                    "max_overtime": spec["max_overtime"],
                    "unit_overtime_cost": spec["unit_overtime_cost"],
                }
            )

    counts = {
        "locations": len(locations),
        "depots": len(depots),
        "clients": len(clients),
        # Every group member is an optional client too -- PyVRP refuses a
        # required one inside a mutually exclusive group -- so the two counts
        # below overlap and `prize_collecting_clients` is the interesting one.
        "optional_clients": sum(1 for c in clients if not c["required"]),
        "prize_collecting_clients": sum(
            1 for c in clients if not c["required"] and c["group"] is None
        ),
        "pickup_clients": sum(1 for c in clients if any(c["pickup"])),
        "released_clients": sum(1 for c in clients if c["release_time"] > 0),
        "grouped_clients": sum(1 for c in clients if c["group"] is not None),
        "tight_window_clients": sum(
            1 for c in clients if c["tw_late"] - c["tw_early"] <= TIGHT_SLOT_WIDTH_S
        ),
        "groups": len(groups),
        "shipments": len(shipments),
        "vehicle_types": len(vehicle_types),
        "vehicles": sum(v["num_available"] for v in vehicle_types),
        "total_delivery_kg": sum(c["delivery"][0] for c in clients),
        "total_delivery_dm3": sum(c["delivery"][1] for c in clients),
    }

    return {
        "meta": {
            "name": f"metro-{orders}-s{seed}",
            "generator": "examples/pyvrp/make_instance.py",
            "format_version": 1,
            "orders": orders,
            "seed": seed,
            "units": {
                "distance": "m",
                "duration": "s",
                "load": ["kg", "dm3"],
                "cost": "1e-4 EUR",
            },
            "horizon": [0, HORIZON_S],
            "time_zero_clock": "06:00",
            "area_m": AREA_M,
            "centre": list(CENTRE),
            "centre_radius_m": CENTRE_RADIUS_M,
            "suburbs": [list(point) for point in suburbs],
            "road_factor": ROAD_FACTOR,
            "profiles": [dict(profile) for profile in PROFILES],
            "counts": counts,
        },
        "locations": locations,
        "depots": depots,
        "clients": clients,
        "groups": groups,
        "shipments": shipments,
        "vehicle_types": vehicle_types,
    }


def _prize(rng: random.Random, service: int) -> int:
    """What a courier partner would charge to take this order off our hands.

    Anchored on the marginal cost of serving it ourselves -- the service time
    at a van's duration cost, plus a 4 km round-trip detour at a van's
    distance-and-time cost -- and then scaled by a factor between 0.5 and 1.8.
    That spread is the point: some optional orders are worth taking and some
    are not, so *which* ones a configuration drops is a real decision rather
    than an all-or-nothing switch.
    """
    van = VEHICLE_CLASSES[0]
    per_metre = van["unit_distance_cost"] + van["unit_duration_cost"] / PROFILES[0]["speed_mps"]
    marginal = service * van["unit_duration_cost"] + 4_000 * per_metre
    return int(round(marginal * rng.uniform(0.5, 1.8)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--orders", type=int, default=3000, help="number of orders")
    parser.add_argument("--seed", type=int, default=7, help="generator seed")
    parser.add_argument("--out", required=True, help="path to write the JSON to")
    args = parser.parse_args(argv)

    instance = generate(args.orders, args.seed)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(instance, indent=1, sort_keys=False), encoding="utf-8", newline="\n"
    )
    counts = instance["meta"]["counts"]
    print(
        f"{instance['meta']['name']}: {counts['clients']} clients "
        f"({counts['optional_clients']} optional, {counts['pickup_clients']} with "
        f"returns, {counts['released_clients']} released, {counts['grouped_clients']} "
        f"in {counts['groups']} group(s), {counts['tight_window_clients']} on a tight "
        f"window), {counts['shipments']} shipment(s), {counts['vehicles']} vehicles of "
        f"{counts['vehicle_types']} type(s) from {counts['depots']} depot(s) "
        f"-> {out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
