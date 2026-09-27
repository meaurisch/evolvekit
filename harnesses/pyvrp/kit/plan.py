"""A PyVRP solution as tables: routes, visits, unassigned and summary.

Costs are in currency. `routes.cost` is what a route costs at the rates of
the untouched request, `routes.solver_cost` at the rates PyVRP saw, after the
study's data changes: the first is what a plan really costs, the second what
PyVRP minimised. `unassigned.prize` is the untouched request's prize too.
"""

from __future__ import annotations

from . import instance as benchmark


class Solved:
    """What `solve` hands to `solution_tables`."""

    def __init__(self, best, built, *, iterations, runtime_s, stopped=False):
        self.best = best
        self.built = built
        self.iterations = iterations
        self.runtime_s = runtime_s
        self.stopped = stopped
        """PyVRP had not come back after the time limit and was stopped (solving.py)."""


def task_id(activity, first_shipment_task):
    """The task a scheduled PyVRP activity serves: clients keep their index,
    shipment j is task C + 2j (pickup) and C + 2j + 1 (delivery)."""
    if activity.is_client():
        return activity.idx
    return first_shipment_task + 2 * activity.idx + (1 if activity.is_delivery() else 0)


def _pricing(data):
    """For a VRPLIB case: a fixed price list for an infeasible result, like
    the benchmark's `infeasibility_pricing`, from a cheap upper bound on any
    feasible plan (every client served on its own round trip, twice over)."""
    types = data.vehicle_types()
    clients = data.clients()
    distance = data.distance_matrix(0)
    duration = data.duration_matrix(0)
    per_metre = max(vt.unit_distance_cost for vt in types)
    per_second = max(vt.unit_duration_cost for vt in types)
    depots = [depot.location for depot in data.depots()]
    offset = sum(vt.num_available * vt.fixed_cost for vt in types)
    offset += sum(c.prize for c in clients) + sum(s.prize for s in data.shipments())
    for c in clients:
        offset += 2 * min(
            per_metre * int(distance[d, c.location] + distance[c.location, d])
            + per_second * (int(duration[d, c.location] + duration[c.location, d]) + c.service_duration)
            for d in depots
        )
    offset = max(1, int(offset))
    dims = data.num_load_dimensions
    capacity = [max(1, sum(vt.num_available * vt.capacity[d] for vt in types)) for d in range(dims)]
    return {
        "load_penalties": [max(1, offset // capacity[d]) for d in range(dims)],
        "tw_penalty": max(1, 100 * max(per_second, 1)),
        "dist_penalty": max(1, 100 * max(per_metre, 1)),
        "missing_penalty": max(1, offset // max(1, len(clients))),
        "offset": offset,
    }


def objective(best, built):
    """PyVRP's cost of the best solution when it is feasible; otherwise a
    number above every feasible cost that is still smaller for smaller
    violations (the benchmark's pricing). In PyVRP's cost units."""
    from pyvrp import CostEvaluator

    data = built.data
    if best.is_feasible():
        return int(CostEvaluator([0] * data.num_load_dimensions, 0, 0).cost(best))
    pricing = benchmark.infeasibility_pricing(built.instance) if built.instance is not None else _pricing(data)
    priced = CostEvaluator(pricing["load_penalties"], pricing["tw_penalty"], pricing["dist_penalty"])
    missing = best.num_missing_clients() + best.num_missing_groups() + best.num_missing_shipments()
    return pricing["offset"] + int(priced.penalised_cost(best)) + missing * pricing["missing_penalty"]


def tables(req, solved):
    """The solution tables of `solved`, a solution of `req` after its data changes."""
    best, built = solved.best, solved.built
    data = built.data
    tasks = {row["task_id"]: row for row in req.original["tasks"]}
    types = {row["type_id"]: row for row in req.original["vehicle_types"]}
    to_money = req.cost_unit / req.scale
    first = data.num_clients
    routes, visits, served = [], [], set()
    for number, route in enumerate(best.routes(), start=1):
        type_id = built.type_ids[route.vehicle_type()]
        vt = types[type_id]
        stops = [(a, task_id(a, first)) for a in route.schedule() if not a.is_depot()]
        by_trip = {}
        for activity, tid in stops:
            by_trip.setdefault(activity.trip, []).append(tid)
        neighbours = {}
        for tids in by_trip.values():
            for at, tid in enumerate(tids):
                neighbours[tid] = (tids[at - 1] if at else None, tids[at + 1] if at + 1 < len(tids) else None)
        # A trip leaves the depot with its clients' deliveries on board (and,
        # on the first trip, the vehicle's initial load); shipments are
        # picked up on the way.
        load = {
            trip: sum(tasks[t]["delivery_kg"] for t in tids if tasks[t]["shipment_id"] is None)
            + (vt["initial_kg"] if trip == 0 else 0)
            for trip, tids in by_trip.items()
        }
        for seq, (activity, tid) in enumerate(stops, start=1):
            load[activity.trip] += tasks[tid]["pickup_kg"] - tasks[tid]["delivery_kg"]
            served.add(tid)
            visits.append({
                "route_id": number, "seq": seq, "trip": activity.trip + 1, "task_id": tid,
                "kind": tasks[tid]["kind"], "start_s": int(activity.start_time), "end_s": int(activity.end_time),
                "wait_s": int(activity.wait_duration), "late_s": int(activity.time_warp),
                "load_kg": int(load[activity.trip]),
                "prev_task_id": neighbours[tid][0], "next_task_id": neighbours[tid][1],
            })
        distance, duration, overtime = int(route.distance()), int(route.duration()), int(route.overtime())
        cost = (vt["fixed_cost"] + vt["unit_distance_cost"] * distance / 1000
                + vt["unit_duration_cost"] * duration / 3600 + vt["unit_overtime_cost"] * overtime / 3600)
        seen = route.fixed_vehicle_cost() + route.distance_cost() + route.duration_cost()
        routes.append({
            "route_id": number, "type_id": type_id, "class": vt["class"],
            "start_s": int(route.start_time()), "end_s": int(route.end_time()), "duration_s": duration,
            "distance_m": distance, "overtime_s": overtime, "travel_s": int(route.travel_duration()),
            "service_s": int(route.service_duration()), "wait_s": int(route.wait_duration()),
            "time_warp_s": int(route.time_warp()), "trips": int(route.num_trips()), "stops": len(stops),
            "cost": round(cost, 6), "solver_cost": round(seen * to_money, 6),
        })
    unassigned = [
        {"task_id": tid, "kind": row["kind"], "required": row["required"], "group_id": row["group_id"],
         "prize": row["prize"]}
        for tid, row in sorted(tasks.items()) if tid not in served
    ]
    summary = [{
        "solver_objective": round(objective(best, built) * to_money, 6),
        "feasible": bool(best.is_feasible()),
        "missed_required": int(best.num_missing_clients() + best.num_missing_groups() + best.num_missing_shipments()),
        "iterations": int(solved.iterations),
        "runtime_s": round(solved.runtime_s, 3),
        "stopped": bool(solved.stopped),
    }]
    return {"routes": routes, "visits": visits, "unassigned": unassigned, "summary": summary}
