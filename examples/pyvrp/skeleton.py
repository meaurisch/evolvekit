"""PyVRP configuration search: find the `SolveParams` that solve this instance best.

The problem is not "write a VRP heuristic" -- PyVRP already is one, and a very
good one. The problem is: **given a fixed instance, a fixed solver and a hard
15-minute wall clock, which configuration of that solver finds the cheapest
feasible routing?** Everything above the fence is frozen and does the same
thing for every candidate:

* `load_instance()` reads the JSON written by `make_instance.py`.
* `build_data()` turns it into a `pyvrp.ProblemData`, deriving the two travel
  matrices per routing profile from the coordinates, the road factor and the
  profile speeds. This is where the modelling lives, and no candidate can
  touch it: two candidates always solve *the same* instance.
* `enforce_time_limit()` is the referee. The solve budget is
  `min(requested, MAX_TIME_LIMIT_S)` seconds, and the time `configure()`
  itself spends is deducted from it, so a candidate that builds an expensive
  warm start pays for it out of its own budget rather than the framework's.
* `run_solve()` calls the block, runs `pyvrp.solve()` and measures the result.
  A block that raises, or that returns something that is not a `SolveParams`,
  is *counted* (`config_errors`) and replaced by PyVRP's defaults -- never
  fatal, so the search still gets a score and a gradient in the region where
  most first attempts land.

Below the fence, `configure(data, time_limit, seed)` returns the configuration.
See its docstring for every tunable, its default and its meaning.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path

import numpy as np
import pyvrp
from pyvrp import (
    Client,
    ClientGroup,
    CostEvaluator,
    Depot,
    IteratedLocalSearchParams,
    Location,
    PenaltyParams,
    ProblemData,
    Shipment,
    SolveParams,
    VehicleType,
)
from pyvrp.search import (  # noqa: F401 - re-exported for the evolve block
    OPERATORS,
    InsertOptionalClient,
    InsertOptionalShipment,
    NeighbourhoodParams,
    PerturbationParams,
    Relocate1,
    Relocate2,
    Relocate3,
    RelocateAlternative,
    RelocateDelivery,
    RelocatePickup,
    RelocateShipment,
    RelocateWithDepot,
    RemoveAdjacentDepot,
    RemoveOptionalClient,
    RemoveOptionalShipment,
    ReplaceGroup,
    ReplaceOptionalClient,
    ReplaceOptionalShipment,
    Swap11,
    Swap21,
    Swap22,
    Swap31,
    Swap32,
    Swap33,
    SwapTails,
)
from pyvrp.stop import MaxRuntime

__all__ = [
    "MAX_TIME_LIMIT_S",
    "MIN_SOLVE_S",
    "load_instance",
    "build_data",
    "enforce_time_limit",
    "penalty_scale",
    "run_solve",
    "configure",
]

MAX_TIME_LIMIT_S = 900.0
"""The hard cap, in seconds: fifteen minutes per solve, whatever is asked for.

Enforced in `enforce_time_limit()`, which the evolve block cannot reach. The
stage timeout in the YAML is set above this so that the cap, not the timeout,
is what ends a long run -- a killed stage produces no KPIs at all, which is a
worse signal than a capped one.
"""

MIN_SOLVE_S = 1.0
"""Floor on the solve budget left after `configure()` has had its turn."""

# --------------------------------------------------------------------------
# the instance
# --------------------------------------------------------------------------


def load_instance(path: str | Path) -> dict:
    """Read an instance written by `make_instance.py`."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _travel_matrices(instance: dict) -> tuple[list[np.ndarray], list[np.ndarray]]:
    """Distance and duration matrices, one pair per routing profile.

    Distance is Euclidean x `road_factor`, rounded to whole metres. A profile
    may additionally inflate every edge that touches the inner-city zone by
    `centre_distance_multiplier`: a 12-tonne truck does not take the streets a
    van takes, and that is what makes the second profile more than a slower
    copy of the first. Duration is that profile's distance divided by that
    profile's door-to-door speed, in whole seconds.
    """
    meta = instance["meta"]
    points = np.asarray(instance["locations"], dtype=np.float64)
    dx = points[:, 0][:, None] - points[:, 0][None, :]
    dy = points[:, 1][:, None] - points[:, 1][None, :]
    base = np.hypot(dx, dy) * float(meta["road_factor"])

    centre = np.asarray(meta["centre"], dtype=np.float64)
    inner = (
        np.hypot(points[:, 0] - centre[0], points[:, 1] - centre[1])
        <= float(meta["centre_radius_m"])
    )
    touches_centre = inner[:, None] | inner[None, :]

    distances: list[np.ndarray] = []
    durations: list[np.ndarray] = []
    for profile in meta["profiles"]:
        scaled = base.copy()
        multiplier = float(profile.get("centre_distance_multiplier", 1.0))
        if multiplier != 1.0:
            scaled[touches_centre] *= multiplier
        distance = np.rint(scaled).astype(np.int64)
        np.fill_diagonal(distance, 0)
        duration = np.rint(scaled / float(profile["speed_mps"])).astype(np.int64)
        np.fill_diagonal(duration, 0)
        distances.append(distance)
        durations.append(duration)
    return distances, durations


def build_data(instance: dict) -> ProblemData:
    """Turn the JSON instance into a `pyvrp.ProblemData`. Frozen, on purpose."""
    locations = [Location(x=int(x), y=int(y)) for x, y in instance["locations"]]
    depots = [
        Depot(
            location=int(d["location"]),
            tw_early=int(d["tw_early"]),
            tw_late=int(d["tw_late"]),
            service_duration=int(d["service_duration"]),
            name=str(d.get("name", "")),
        )
        for d in instance["depots"]
    ]
    clients = [
        Client(
            location=int(c["location"]),
            delivery=[int(v) for v in c["delivery"]],
            pickup=[int(v) for v in c["pickup"]],
            service_duration=int(c["service_duration"]),
            tw_early=int(c["tw_early"]),
            tw_late=int(c["tw_late"]),
            release_time=int(c["release_time"]),
            prize=int(c["prize"]),
            required=bool(c["required"]),
            group=None if c["group"] is None else int(c["group"]),
            name=str(c.get("name", "")),
        )
        for c in instance["clients"]
    ]
    groups = [
        ClientGroup(
            clients=[int(i) for i in g["clients"]],
            required=bool(g["required"]),
            name=str(g.get("name", "")),
        )
        for g in instance["groups"]
    ]
    shipments = [
        Shipment(
            pickup_location=int(s["pickup_location"]),
            delivery_location=int(s["delivery_location"]),
            pickup_tw_early=int(s["pickup_tw_early"]),
            pickup_tw_late=int(s["pickup_tw_late"]),
            pickup_service_duration=int(s["pickup_service_duration"]),
            delivery_tw_early=int(s["delivery_tw_early"]),
            delivery_tw_late=int(s["delivery_tw_late"]),
            delivery_service_duration=int(s["delivery_service_duration"]),
            amount=[int(v) for v in s["amount"]],
            prize=int(s["prize"]),
            required=bool(s["required"]),
            name=str(s.get("name", "")),
        )
        for s in instance["shipments"]
    ]
    vehicle_types = [
        VehicleType(
            num_available=int(v["num_available"]),
            capacity=[int(c) for c in v["capacity"]],
            start_depot=int(v["start_depot"]),
            end_depot=int(v["end_depot"]),
            fixed_cost=int(v["fixed_cost"]),
            tw_early=int(v["tw_early"]),
            tw_late=int(v["tw_late"]),
            shift_duration=int(v["shift_duration"]),
            max_distance=int(v["max_distance"]),
            unit_distance_cost=int(v["unit_distance_cost"]),
            unit_duration_cost=int(v["unit_duration_cost"]),
            profile=int(v["profile"]),
            start_late=int(v["start_late"]),
            reload_depots=[int(d) for d in v["reload_depots"]],
            max_reloads=int(v["max_reloads"]),
            max_overtime=int(v["max_overtime"]),
            unit_overtime_cost=int(v["unit_overtime_cost"]),
            name=str(v.get("name", "")),
        )
        for v in instance["vehicle_types"]
    ]
    distances, durations = _travel_matrices(instance)
    return ProblemData(
        locations, clients, depots, vehicle_types, distances, durations, groups, shipments
    )


# --------------------------------------------------------------------------
# the referee
# --------------------------------------------------------------------------


def enforce_time_limit(requested: float) -> float:
    """`min(requested, MAX_TIME_LIMIT_S)`, and never below `MIN_SOLVE_S`.

    The only place the 15-minute rule lives. It is above the fence because a
    configuration that wins by taking an hour has not won anything.
    """
    try:
        value = float(requested)
    except (TypeError, ValueError):
        value = MAX_TIME_LIMIT_S
    if not math.isfinite(value) or value <= 0.0:
        value = MAX_TIME_LIMIT_S
    return max(MIN_SOLVE_S, min(value, MAX_TIME_LIMIT_S))


def penalty_scale(instance: dict) -> tuple[list[int], int, int]:
    """Penalties used to price an *infeasible* solution, in cost units.

    Derived from the fleet so they are a property of the instance and not of
    the candidate: a unit of excess load costs what the most expensive vehicle
    charges to carry a unit of capacity; a second of time warp costs what the
    most expensive driver-second costs; a metre over the distance limit costs
    what the most expensive metre costs. Each term is the *marginal* cost of
    the capacity, the time or the distance the solution is short of, so
    `penalised_cost` is a defensible gradient rather than an arbitrary big-M.
    It is the number reported when no feasible solution was found; the "an
    infeasible solution must not outrank a feasible one" part of the job is
    done by the `infeasible` KPI and its weight in the config, not here.
    """
    types = instance["vehicle_types"]
    fixed = max(int(v["fixed_cost"]) for v in types)
    dims = len(types[0]["capacity"])
    load = [
        max(1, fixed // max(1, max(int(v["capacity"][k]) for v in types)))
        for k in range(dims)
    ]
    duration = max(int(v["unit_duration_cost"]) for v in types)
    distance = max(int(v["unit_distance_cost"]) for v in types)
    return load, duration, distance


def _normalise(returned: object) -> tuple[SolveParams, object | None, str]:
    """Accept `SolveParams`, or `(SolveParams, initial_solution)`. Or complain."""
    initial = None
    value = returned
    if isinstance(returned, tuple):
        if len(returned) != 2:
            return SolveParams(), None, "configure() returned a tuple of length != 2"
        value, initial = returned
    if not isinstance(value, SolveParams):
        return (
            SolveParams(),
            None,
            f"configure() returned {type(value).__name__}, expected SolveParams",
        )
    if initial is not None and not isinstance(initial, pyvrp.Solution):
        return value, None, "configure() returned a non-Solution warm start"
    return value, initial, ""


def _convergence(result, budget: float) -> list[float]:
    """Best cost at 25 / 50 / 75 / 100 % of the solve budget.

    `Statistics.runtimes` holds one entry per iteration, so their running sum
    is the elapsed time. `inf` means no feasible-or-otherwise best had been
    recorded yet at that point, and is reported as `-1.0` so the KPI stays a
    finite number.
    """
    stats = getattr(result, "stats", None)
    data = list(getattr(stats, "data", []) or [])
    runtimes = list(getattr(stats, "runtimes", []) or [])
    if not data or not runtimes:
        return [-1.0, -1.0, -1.0, -1.0]
    elapsed = 0.0
    marks = [budget * f for f in (0.25, 0.5, 0.75, 1.0)]
    out: list[float] = []
    index = 0
    for mark in marks:
        while index + 1 < len(data) and elapsed + runtimes[index] <= mark:
            elapsed += runtimes[index]
            index += 1
        cost = data[min(index, len(data) - 1)].best_cost
        out.append(float(cost) if math.isfinite(cost) else -1.0)
    return out


def run_solve(
    instance: dict,
    *,
    time_limit: float,
    seed: int,
    data: ProblemData | None = None,
) -> dict:
    """Configure, solve, measure. Returns a plain dict of numbers and strings."""
    budget = enforce_time_limit(time_limit)
    if data is None:
        data = build_data(instance)

    config_errors = 0.0
    note = ""
    started = time.perf_counter()
    try:
        returned = configure(data, budget, int(seed))
    except Exception as exc:  # noqa: BLE001 - a crash is counted, never fatal
        config_errors = 1.0
        returned = None
        note = f"configure() raised {type(exc).__name__}: {exc}"
    params, initial, complaint = _normalise(returned)
    if complaint:
        config_errors = 1.0
        note = note or complaint
    configure_s = time.perf_counter() - started

    # The candidate's own set-up time comes out of the candidate's budget.
    remaining = max(MIN_SOLVE_S, budget - configure_s)
    solve_started = time.perf_counter()
    try:
        result = pyvrp.solve(
            data,
            stop=MaxRuntime(remaining),
            seed=int(seed),
            collect_stats=True,
            display=False,
            params=params,
            initial_solution=initial,
        )
    except Exception as exc:  # noqa: BLE001 - counted, then retried on defaults
        # Not every bad configuration can be spotted before the solve: an
        # operator list holding something that is not an operator, or a warm
        # start built for a different instance, only fails once PyVRP looks at
        # it. Count it and give the candidate the defaults, so the cascade
        # still gets a score instead of a dead stage.
        config_errors = 1.0
        note = note or f"pyvrp.solve() raised {type(exc).__name__}: {exc}"
        params, initial = SolveParams(), None
        remaining = max(MIN_SOLVE_S, budget - (time.perf_counter() - started))
        result = pyvrp.solve(
            data,
            stop=MaxRuntime(remaining),
            seed=int(seed),
            collect_stats=True,
            display=False,
        )
    solve_s = time.perf_counter() - solve_started

    best = result.best
    loads, tw, dist = penalty_scale(instance)
    evaluator = CostEvaluator(loads, tw, dist)
    feasible = bool(best.is_feasible())
    penalised = float(evaluator.penalised_cost(best))

    routes = list(best.routes())
    lengths = [route.num_clients() for route in routes]
    by_type: dict[int, int] = {}
    for route in routes:
        by_type[route.vehicle_type()] = by_type.get(route.vehicle_type(), 0) + 1

    # `num_missing_clients()` counts only *required* clients, which should
    # always be zero in a feasible solution. What we want besides that is how
    # many prize-collecting orders the configuration decided to drop -- and to
    # not count the group members it was never allowed to visit, since exactly
    # one address per mutually exclusive group is unvisited by construction.
    grouped = {int(i) for group in instance["groups"] for i in group["clients"]}
    unplanned = [a.idx for a in best.unplanned() if a.is_client]
    skipped_optional = sum(
        1
        for index in unplanned
        if index not in grouped and not instance["clients"][index]["required"]
    )

    return {
        "feasible": feasible,
        "objective": float(result.cost()) if feasible else penalised,
        "penalised_cost": penalised,
        "distance": float(best.distance()),
        "duration": float(best.duration()),
        "num_routes": float(best.num_routes()),
        "num_trips": float(best.num_trips()),
        "fixed_cost": float(best.fixed_vehicle_cost()),
        "distance_cost": float(best.distance_cost()),
        "duration_cost": float(best.duration_cost()),
        "excess_load": [float(v) for v in best.excess_load()],
        "excess_distance": float(best.excess_distance()),
        "time_warp": float(best.time_warp()),
        "overtime": float(best.overtime()),
        "uncollected_prizes": float(best.uncollected_prizes()),
        "unassigned_clients": float(skipped_optional),
        "missing_required_clients": float(best.num_missing_clients()),
        "unassigned_shipments": float(best.num_missing_shipments()),
        "missing_groups": float(best.num_missing_groups()),
        "clients_visited": float(best.num_clients()),
        "mean_route_length": (sum(lengths) / len(lengths)) if lengths else 0.0,
        "route_lengths": lengths,
        "fleet_mix": by_type,
        "iterations": float(result.num_iterations),
        "budget_s": budget,
        "configure_s": configure_s,
        "solve_s": solve_s,
        "time_used_s": configure_s + solve_s,
        "config_errors": config_errors,
        "config_note": note,
        "convergence": _convergence(result, remaining),
        "warm_started": initial is not None,
    }


# EVOLVE-BLOCK-START
# PARAMS: {"NUM_NEIGHBOURS": [8.0, 120.0], "WEIGHT_WAIT_TIME": [0.0, 2.0], "MIN_PERTURBATIONS": [1.0, 20.0], "MAX_PERTURBATIONS": [4.0, 80.0], "HISTORY_LENGTH": [10.0, 3000.0], "SOLUTIONS_BETWEEN_UPDATES": [20.0, 4000.0], "PENALTY_INCREASE": [1.01, 5.0], "PENALTY_DECREASE": [0.1, 0.99], "TARGET_FEASIBLE": [0.05, 0.95], "MAX_PENALTY": [1000.0, 10000000.0]}
NUM_NEIGHBOURS = 50.0
WEIGHT_WAIT_TIME = 0.2
MIN_PERTURBATIONS = 1.0
MAX_PERTURBATIONS = 25.0
HISTORY_LENGTH = 300.0
SOLUTIONS_BETWEEN_UPDATES = 500.0
PENALTY_INCREASE = 1.5
PENALTY_DECREASE = 0.9
TARGET_FEASIBLE = 0.65
MAX_PENALTY = 100000.0


def configure(data, time_limit, seed):
    """Return the `SolveParams` PyVRP should use on `data` within `time_limit`.

    The seed is PyVRP 0.14.0's own defaults, spelled out rather than inherited,
    so that every knob is visible in the block being evolved. `data` is a fully
    built `pyvrp.ProblemData`, so a candidate may *look at the instance* --
    `data.num_clients`, `data.num_vehicle_types`, `data.num_load_dimensions`,
    `data.distance_matrix(profile)` -- and configure differently for a big
    instance than for a small one. `time_limit` is the solve budget in seconds
    (already capped at 900) and `seed` is the RNG seed the solver will use.

    May return either a `SolveParams`, or a `(SolveParams, initial_solution)`
    pair where the second element is a `pyvrp.Solution` warm start or `None`.
    Anything else is counted as a `config_errors` and replaced by the defaults.
    Whatever time this function spends is deducted from the solve budget.

    THE TUNABLES, with PyVRP 0.14.0's defaults
    ------------------------------------------
    `SolveParams(ils=..., penalty=..., neighbourhood=..., operators=...,
                 perturbation=..., display_interval=5.0)`

    IteratedLocalSearchParams
      num_iters_no_improvement = 150000
          Iterations without an improvement before the search restarts from
          the best-known solution. Small values restart often (diversify);
          large values never restart (intensify). On a 900 s run of a 3,000-
          client instance PyVRP does on the order of 10^4-10^5 iterations, so
          the default is effectively "never restart".
      history_length = 300
          Length of the late-acceptance hill-climbing history. The candidate
          is accepted if it beats the solution from `history_length`
          iterations ago, so a long history accepts more worsening moves.
      exhaustive_on_best = True
          Whether a new best solution gets a second, more expensive full local
          search pass. Expensive per hit; on a big instance it can be most of
          the iteration budget.

    PenaltyParams -- how load/duration/distance violations are priced during
    the search (not in the reported objective, which is fixed above the fence)
      solutions_between_updates = 500   registrations between penalty updates
      penalty_increase = 1.5            multiplier when too few feasible
      penalty_decrease = 0.9            multiplier when too many feasible
      target_feasible = 0.65            target share of feasible registrations
      feas_tolerance = 0.05             deadband around that share
      min_penalty = 0.1, max_penalty = 100000.0
          Clamps. On this instance one cost unit is 1e-4 EUR, so the
          default ceiling of 100,000 is worth EUR 10 per second of time warp
          or per kilogram of excess load. That is ample against a EUR 35 van,
          but `max_penalty` is still the knob that decides whether a search
          that has left feasibility can price its way back.

    NeighbourhoodParams -- the granular neighbourhood the local search uses
      num_neighbours = 50        arcs kept per client; the single biggest
                                 iteration-cost/quality trade-off there is
      weight_wait_time = 0.2     how much waiting time counts in proximity;
                                 raise it when time windows bind
      symmetric_proximity = True whether (i, j) and (j, i) get equal weight

    PerturbationParams -- the ruin step between local searches
      min_perturbations = 1, max_perturbations = 25
          How much of the incumbent is destroyed before it is rebuilt. Small =
          fine-grained intensification; large = big jumps.

    operators -- the list of local-search operator *classes*. The default is
    `pyvrp.search.OPERATORS`: Relocate1, Relocate2, Swap11, Swap21, Swap22,
    SwapTails, RelocateAlternative, RelocatePickup, RelocateDelivery,
    RelocateWithDepot, RemoveAdjacentDepot, RemoveOptionalClient,
    InsertOptionalClient, ReplaceOptionalClient, RemoveOptionalShipment,
    InsertOptionalShipment, ReplaceOptionalShipment, ReplaceGroup and
    RelocateShipment. Also importable and *not* on by default: Relocate3,
    Swap31, Swap32, Swap33 -- wider exchanges that cost more per iteration.
    Operators whose `supports(data)` is False are skipped automatically, so an
    over-long list is wasteful rather than wrong.

    WHAT COUNTS AS A DIFFERENT DECISION
    -----------------------------------
    Structure, not constant-nudging. `num_neighbours = 49` instead of 50 is
    the same configuration. These are different configurations:
      * dropping the expensive wide-exchange operators to buy iterations, or
        adding Relocate3/Swap33 to buy move quality;
      * turning `exhaustive_on_best` off and spending the time on more
        perturbation instead;
      * making the penalty schedule aggressive (large increase, low target
        feasible) so the search lives in the infeasible region, or the
        opposite;
      * scaling `num_neighbours`, `max_perturbations` or
        `num_iters_no_improvement` *as a function of* `data.num_clients` or of
        `time_limit`, so one block covers the 300-order proxy and the 3,000-
        order instance;
      * building a warm start and returning it alongside the params.
    """
    return SolveParams(
        ils=IteratedLocalSearchParams(
            num_iters_no_improvement=150_000,
            history_length=int(HISTORY_LENGTH),
            exhaustive_on_best=True,
        ),
        penalty=PenaltyParams(
            solutions_between_updates=int(SOLUTIONS_BETWEEN_UPDATES),
            penalty_increase=float(PENALTY_INCREASE),
            penalty_decrease=float(PENALTY_DECREASE),
            target_feasible=float(TARGET_FEASIBLE),
            feas_tolerance=0.05,
            min_penalty=0.1,
            max_penalty=float(MAX_PENALTY),
        ),
        neighbourhood=NeighbourhoodParams(
            weight_wait_time=float(WEIGHT_WAIT_TIME),
            num_neighbours=max(1, int(NUM_NEIGHBOURS)),
            symmetric_proximity=True,
        ),
        operators=list(OPERATORS),
        perturbation=PerturbationParams(
            # Clamped rather than trusted: `param_lhs` draws these two from
            # overlapping ranges and PyVRP refuses a maximum below its minimum.
            min_perturbations=max(0, int(MIN_PERTURBATIONS)),
            max_perturbations=max(1, int(MAX_PERTURBATIONS), int(MIN_PERTURBATIONS)),
        ),
        display_interval=1e9,
    )
# EVOLVE-BLOCK-END
