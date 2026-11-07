"""Requests: reading a case, the tables a study sees, and the request PyVRP
solves once the study's data changes are applied.

Two kinds of case:

* **JSON**, in the pyvrp_hard benchmark's format (`pyvrp-hard/1`; see
  README.md) or as `pyvrp-request/1`: the same, plus optional `tags` (a list
  of words) on clients, shipments and vehicle types, an optional `time_zero`
  (the clock time of t = 0, "06:00") and `cost_unit` (currency per cost unit,
  0.0001 unless it says otherwise), and costs that may carry a decimal.
* **VRPLIB** files, read with `pyvrp.read(path, round_func="round")`. Their
  tables are shown and their settings tuned, but no data change applies to
  them: they have no vehicle classes or tags to select by.

Units in the tables: metres, seconds from the request's time zero, kilograms
(`_kg`), cubic decimetres (`_dm3`) and cold boxes (`_boxes`), and currency --
a fixed cost per vehicle used, a distance cost per kilometre, duration and
overtime costs per hour, prizes.

PyVRP computes with integers, and a van's distance cost in the benchmark is
the integer 3 (0.30 per km): a data change that scaled it by 0.9 would get 3
again. So PyVRP sees every cost RESOLUTION times finer than the request
states it, and the settings measured in cost units are scaled alike
(params.COST_UNIT_SETTINGS): every setting still means what it means for the
request as written.
"""

from __future__ import annotations

import copy
import json
import math
import os
import re

import evk_harness as evk

from . import instance as benchmark

FORMATS = ("pyvrp-hard/1", "pyvrp-request/1")
WRITTEN = "pyvrp-request/1"
DEFAULT_COST_UNIT = 1e-4
RESOLUTION = 10
FORBIDDEN = 10_000_000
UNBOUNDED = 1 << 62
"""PyVRP spells "no limit" as the largest int64 (or uint64): the tables say NULL."""
LOADS = ("kg", "dm3", "boxes")
REQUIRED_KEYS = ("locations", "depots", "clients", "vehicle_types", "profiles", "groups", "shipments",
                 "zone", "restricted_locations")
VEHICLE_KEYS = {
    "num_available", "capacity", "start_depot", "end_depot", "fixed_cost", "tw_early", "tw_late",
    "shift_duration", "max_distance", "unit_distance_cost", "unit_duration_cost", "profile", "start_late",
    "initial_load", "reload_depots", "max_reloads", "max_overtime", "unit_overtime_cost", "name", "class", "tags",
}
COSTS = (
    # table column, request key, what one unit of the column is, file units per column unit
    ("fixed_cost", "fixed_cost", "per vehicle", 1.0),
    ("unit_distance_cost", "unit_distance_cost", "per km", 1000.0),
    ("unit_duration_cost", "unit_duration_cost", "per hour", 3600.0),
    ("unit_overtime_cost", "unit_overtime_cost", "per hour", 3600.0),
)


class Request:
    """A case as the runner keeps it: the request as read, and what it takes
    to rebuild it from the (changed) tables."""

    def __init__(self, kind, name, *, instance=None, data=None, cost_unit=1.0, time_zero_s=0, fmt=""):
        self.kind = kind
        """`json` or `vrplib`."""
        self.name = name
        self.instance = instance
        """JSON: the request as read. Never changed."""
        self.data = data
        """VRPLIB: the `pyvrp.ProblemData`."""
        self.cost_unit = cost_unit
        """Currency per cost unit of the request file."""
        self.scale = RESOLUTION if kind == "json" else 1
        """PyVRP's cost unit is the file's divided by this."""
        self.time_zero_s = time_zero_s
        self.format = fmt
        self.original = None
        """The tables as read, before any data change (read_case sets them)."""

    # file units <-> table units ---------------------------------------------

    def money(self, value, per=1.0):
        """A cost in file units (per metre, per second, ...) as currency (per km, per hour, ...)."""
        return round(float(value) * self.cost_unit * per, 9)

    def internal(self, value, per=1.0):
        """A table cost as the integer PyVRP sees."""
        return int(round(float(value) / (self.cost_unit * per) * self.scale))

    def in_file(self, value, per=1.0):
        """A table cost in file units, to a tenth: what a written request holds."""
        number = round(float(value) / (self.cost_unit * per) * RESOLUTION) / RESOLUTION
        return int(number) if number == int(number) else number


def _bounded(value):
    if value is None:
        return None
    value = int(value)
    return None if value >= UNBOUNDED else value


def _clock(text, where):
    match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(text))
    if not match or int(match.group(1)) > 23 or int(match.group(2)) > 59:
        raise ValueError("{}: a clock time such as \"06:00\", got {!r}".format(where, text))
    return int(match.group(1)) * 3600 + int(match.group(2)) * 60


def _tags(item, where):
    tags = item.get("tags") or []
    if not isinstance(tags, list) or not all(isinstance(t, str) and t.strip() for t in tags):
        raise ValueError("{}: `tags` must be a list of words, got {!r}".format(where, tags))
    return [t.strip() for t in tags]


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------


def read(path):
    """The request at `path`: JSON by its extension, VRPLIB otherwise."""
    if os.path.splitext(path)[1].lower() == ".json":
        return _read_json(path)
    return _read_vrplib(path)


def _read_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict):
        raise ValueError("a request is a JSON object")
    fmt = raw.get("format")
    if fmt not in FORMATS:
        raise ValueError("not a PyVRP request: its format is {!r}, expected {}".format(fmt, " or ".join(FORMATS)))
    missing = [key for key in REQUIRED_KEYS if key not in raw]
    if missing:
        raise ValueError("the request has no {}".format(", ".join(repr(k) for k in missing)))
    if not raw["vehicle_types"]:
        raise ValueError("the request has no vehicle types")
    units = ((raw.get("meta") or {}).get("units") or {})
    if fmt == "pyvrp-hard/1":
        cost_unit, time_zero = DEFAULT_COST_UNIT, units.get("time_zero", "00:00")
    else:
        cost_unit = raw.get("cost_unit", DEFAULT_COST_UNIT)
        time_zero = raw.get("time_zero", units.get("time_zero", "00:00"))
        if isinstance(cost_unit, bool) or not isinstance(cost_unit, (int, float)) or not 0 < cost_unit < math.inf:
            raise ValueError("`cost_unit` must be a positive number (currency per cost unit), got {!r}".format(cost_unit))
    dims = len(raw["vehicle_types"][0]["capacity"])
    if not 1 <= dims <= len(LOADS):
        raise ValueError("vehicle capacities need one to three load dimensions (kg, dm3, cold boxes), got {}".format(dims))
    for k, vt in enumerate(raw["vehicle_types"]):
        unknown = sorted(set(vt) - VEHICLE_KEYS)
        if unknown:
            raise ValueError("vehicle type {} has unknown key(s) {}".format(k, ", ".join(unknown)))
        _tags(vt, "vehicle type {}".format(k))
    for i, client in enumerate(raw["clients"]):
        _tags(client, "client {}".format(i))
    for j, shipment in enumerate(raw["shipments"]):
        _tags(shipment, "shipment {}".format(j))
    name = str(raw.get("name") or os.path.splitext(os.path.basename(path))[0])
    return Request("json", name, instance=raw, cost_unit=float(cost_unit),
                   time_zero_s=_clock(time_zero, "time_zero"), fmt=fmt)


def _read_vrplib(path):
    import pyvrp

    try:
        data = pyvrp.read(path, round_func="round")
    except Exception as exc:  # noqa: BLE001 - whatever the reader says, the case cannot be read
        raise ValueError("not a VRPLIB instance PyVRP can read: {}".format(exc)) from None
    name = os.path.splitext(os.path.basename(path))[0]
    return Request("vrplib", name, data=data, cost_unit=1.0, fmt="vrplib")


# ---------------------------------------------------------------------------
# tables
# ---------------------------------------------------------------------------


def _loads(prefix, amounts):
    return {"{}_{}".format(prefix, unit): (int(amounts[i]) if i < len(amounts) else 0) for i, unit in enumerate(LOADS)}


def _task(task_id, kind, location, xy, early, late, release, service, delivery, pickup, prize, required, group, shipment):
    row = {"task_id": task_id, "kind": kind, "location": location, "x_m": int(xy[0]), "y_m": int(xy[1]),
           "tw_early_s": int(early), "tw_late_s": _bounded(late), "release_s": int(release), "service_s": int(service)}
    row.update(_loads("delivery", delivery))
    row.update(_loads("pickup", pickup))
    row.update(prize=prize, required=bool(required), group_id=group, shipment_id=shipment)
    return row


def _client_kind(delivery, pickup):
    gives, takes = any(delivery), any(pickup)
    return "exchange" if gives and takes else "delivery" if gives else "pickup" if takes else "visit"


def tables(req):
    """Every request table of `req`: request, tasks, task_tags, vehicle_types,
    vehicle_tags, depots."""
    return _tables_json(req) if req.kind == "json" else _tables_vrplib(req)


def _tables_json(req):
    inst = req.instance
    locations = inst["locations"]
    clients = inst["clients"]
    dims = len(inst["vehicle_types"][0]["capacity"])
    first = len(clients)
    tasks, task_tags = [], []
    for i, c in enumerate(clients):
        tasks.append(_task(i, _client_kind(c["delivery"], c["pickup"]), c["location"], locations[c["location"]],
                           c["tw_early"], c["tw_late"], c["release_time"], c["service_duration"], c["delivery"],
                           c["pickup"], req.money(c["prize"]), c["required"], c["group"], None))
        task_tags += [{"task_id": i, "tag": tag} for tag in _tags(c, "")]
    zero = [0] * dims
    for j, s in enumerate(inst["shipments"]):
        for side, kind in (("pickup", "shipment_pickup"), ("delivery", "shipment_delivery")):
            task_id = first + 2 * j + (side == "delivery")
            location = s[side + "_location"]
            tasks.append(_task(
                task_id, kind, location, locations[location], s[side + "_tw_early"], s[side + "_tw_late"], 0,
                s[side + "_service_duration"], s["amount"] if side == "delivery" else zero,
                s["amount"] if side == "pickup" else zero, req.money(s["prize"]) if side == "delivery" else 0.0,
                s["required"], None, j,
            ))
            task_tags += [{"task_id": task_id, "tag": tag} for tag in _tags(s, "")]
    profiles = [str(p.get("name", i)) for i, p in enumerate(inst["profiles"])]
    vehicle_types, vehicle_tags = [], []
    for k, vt in enumerate(inst["vehicle_types"]):
        row = {"type_id": k, "name": str(vt.get("name", "")), "class": str(vt.get("class", "")),
               "profile": profiles[vt.get("profile", 0)], "num_available": int(vt.get("num_available", 1))}
        row.update(_loads("capacity", vt["capacity"]))
        row["initial_kg"] = int((vt.get("initial_load") or [0])[0])
        row.update(start_depot=int(vt.get("start_depot", 0)), end_depot=int(vt.get("end_depot", 0)))
        for column, key, _, per in COSTS:
            row[column] = req.money(vt.get(key, 1 if key == "unit_distance_cost" else 0), per)
        row.update(
            tw_early_s=int(vt.get("tw_early", 0)), tw_late_s=_bounded(vt.get("tw_late")),
            start_late_s=_bounded(vt.get("start_late")), shift_duration_s=_bounded(vt.get("shift_duration")),
            max_overtime_s=int(vt.get("max_overtime", 0)), max_distance_m=_bounded(vt.get("max_distance")),
            max_reloads=_bounded(vt.get("max_reloads")),
        )
        vehicle_types.append(row)
        vehicle_tags += [{"type_id": k, "tag": tag} for tag in _tags(vt, "")]
    depots = [
        {"depot_id": d, "name": str(dep.get("name", "D{}".format(d))), "x_m": int(locations[dep["location"]][0]),
         "y_m": int(locations[dep["location"]][1]), "tw_early_s": int(dep["tw_early"]),
         "tw_late_s": _bounded(dep["tw_late"]), "service_s": int(dep["service_duration"])}
        for d, dep in enumerate(inst["depots"])
    ]
    request = [{"name": req.name, "format": req.format, "time_zero_h": req.time_zero_s / 3600, "load_dims": dims}]
    return {"request": request, "tasks": tasks, "task_tags": task_tags, "vehicle_types": vehicle_types,
            "vehicle_tags": vehicle_tags, "depots": depots}


def _tables_vrplib(req):
    data = req.data
    locations = data.locations()
    first = data.num_clients
    tasks = []
    for i, c in enumerate(data.clients()):
        xy = (locations[c.location].x, locations[c.location].y)
        tasks.append(_task(i, _client_kind(c.delivery, c.pickup), c.location, xy, c.tw_early, c.tw_late,
                           c.release_time, c.service_duration, c.delivery, c.pickup, req.money(c.prize), c.required,
                           c.group, None))
    for j, s in enumerate(data.shipments()):
        zero = [0] * len(s.amount)
        for side, kind in (("pickup", "shipment_pickup"), ("delivery", "shipment_delivery")):
            step = getattr(s, side)
            xy = (locations[step.location].x, locations[step.location].y)
            tasks.append(_task(first + 2 * j + (side == "delivery"), kind, step.location, xy, step.tw_early,
                               step.tw_late, 0, step.service_duration, s.amount if side == "delivery" else zero,
                               s.amount if side == "pickup" else zero,
                               req.money(s.prize) if side == "delivery" else 0.0, s.required, None, j))
    vehicle_types = []
    for k, vt in enumerate(data.vehicle_types()):
        row = {"type_id": k, "name": str(vt.name), "class": "", "profile": str(vt.profile),
               "num_available": int(vt.num_available)}
        row.update(_loads("capacity", vt.capacity))
        row["initial_kg"] = int((list(vt.initial_load) or [0])[0])
        row.update(start_depot=int(vt.start_depot), end_depot=int(vt.end_depot))
        for column, key, _, per in COSTS:
            row[column] = req.money(getattr(vt, key), per)
        row.update(
            tw_early_s=int(vt.tw_early), tw_late_s=_bounded(vt.tw_late), start_late_s=_bounded(vt.start_late),
            shift_duration_s=_bounded(vt.shift_duration), max_overtime_s=int(vt.max_overtime),
            max_distance_m=_bounded(vt.max_distance), max_reloads=_bounded(vt.max_reloads),
        )
        vehicle_types.append(row)
    depots = [
        {"depot_id": d, "name": str(dep.name or "D{}".format(d)), "x_m": int(locations[dep.location].x),
         "y_m": int(locations[dep.location].y), "tw_early_s": int(dep.tw_early), "tw_late_s": _bounded(dep.tw_late),
         "service_s": int(dep.service_duration)}
        for d, dep in enumerate(data.depots())
    ]
    request = [{"name": req.name, "format": "vrplib", "time_zero_h": 0.0, "load_dims": data.num_load_dimensions}]
    return {"request": request, "tasks": tasks, "task_tags": [], "vehicle_types": vehicle_types,
            "vehicle_tags": [], "depots": depots}


def summary(tables):
    """One line for the app's case list."""
    tasks = tables["tasks"]
    shipments = len({t["shipment_id"] for t in tasks if t["shipment_id"] is not None})
    vehicles = sum(vt["num_available"] for vt in tables["vehicle_types"])
    parts = ["{:,} tasks".format(len(tasks)) + (" ({:,} of them in {} shipments)".format(2 * shipments, shipments) if shipments else "")]
    types = len(tables["vehicle_types"])
    parts.append("{:,} vehicle{} in {} type{}".format(vehicles, "" if vehicles == 1 else "s", types, "" if types == 1 else "s"))
    parts.append("{} depot{}".format(len(tables["depots"]), "" if len(tables["depots"]) == 1 else "s"))
    if tables["task_tags"]:
        parts.append("{} task tags".format(len({t["tag"] for t in tables["task_tags"]})))
    return ", ".join(parts)


# ---------------------------------------------------------------------------
# data changes
# ---------------------------------------------------------------------------


def change_windows(rows, value, mode):
    """The `time_windows` lever: `scale` widens (or narrows) each selected
    task's window around its middle by the factor `value`; `add` moves it by
    `value` seconds. A window never opens before t = 0."""
    if mode not in ("scale", "add"):
        raise evk.InvalidValues("time_windows can widen (scale) or move (add) a window, not {!r}".format(mode))
    value = float(value)
    if not math.isfinite(value) or (mode == "scale" and value < 0):
        raise evk.InvalidValues("time_windows: a {} of {} is not possible".format(
            "factor" if mode == "scale" else "shift", value))
    for row in rows:
        early, late = row["tw_early_s"], row["tw_late_s"]
        if late is None:
            continue  # no window to change
        if mode == "scale":
            middle, half = (early + late) / 2, (late - early) / 2 * value
            early, late = middle - half, middle + half
        else:
            early, late = early + value, late + value
        early = max(0, int(round(early)))
        row["tw_early_s"], row["tw_late_s"] = early, max(early, int(round(late)))


def _whole(value, what):
    number = float(value)
    if not math.isfinite(number):
        raise evk.InvalidValues("the data changes leave {} without a finite value".format(what))
    return int(round(number))


def _changed(req, tables, internal):
    """The request with every column a data change may touch taken from
    `tables`: costs as the integers PyVRP sees (`internal`) or in file units,
    to a tenth (a written request). Returns (instance, the kept type ids);
    PyVRP gets no vehicle type with zero vehicles."""
    inst = copy.deepcopy(req.instance)
    clients, shipments = inst["clients"], inst["shipments"]
    cost = req.internal if internal else req.in_file
    kept, type_ids = [], []
    for row in tables["vehicle_types"]:
        vt = inst["vehicle_types"][row["type_id"]]
        label = "vehicle type {!r}".format(vt.get("name") or row["type_id"])
        count = _whole(row["num_available"], "the number of vehicles of " + label)
        if count < 0:
            raise evk.InvalidValues("the data changes leave {} with {} vehicles".format(label, count))
        for column, key, unit, per in COSTS:
            if row[column] is None or row[column] < 0:
                raise evk.InvalidValues("the data changes leave {} with a negative {} ({} {})".format(
                    label, column.replace("_", " "), row[column], unit))
            vt[key] = cost(row[column], per)
        shift, overtime = row["shift_duration_s"], row["max_overtime_s"]
        for column, value in (("shift_duration_s", shift), ("max_overtime_s", overtime)):
            if value is not None and value < 0:
                raise evk.InvalidValues("the data changes leave {} with a negative {} ({} s)".format(label, column[:-2].replace("_", " "), value))
        if shift is None:
            vt.pop("shift_duration", None)
        else:
            vt["shift_duration"] = _whole(shift, "the shift of " + label)
        vt["max_overtime"] = 0 if overtime is None else _whole(overtime, "the overtime of " + label)
        vt["num_available"] = count
        if count or not internal:
            kept.append(vt)
            type_ids.append(row["type_id"])
    if not kept:
        raise evk.InvalidValues("the data changes leave no vehicle at all")
    inst["vehicle_types"] = kept
    first = len(clients)
    for row in tables["tasks"]:
        task_id = row["task_id"]
        label = "task {}".format(task_id)
        early = _whole(row["tw_early_s"], "the window of " + label)
        late = None if row["tw_late_s"] is None else _whole(row["tw_late_s"], "the window of " + label)
        service = _whole(row["service_s"], "the service time of " + label)
        if early < 0 or (late is not None and late < early):
            raise evk.InvalidValues("the data changes leave {} with the time window {} s to {} s".format(label, early, late))
        if service < 0:
            raise evk.InvalidValues("the data changes leave {} with a negative service time ({} s)".format(label, service))
        if row["prize"] is None or row["prize"] < 0:
            raise evk.InvalidValues("the data changes leave {} with a negative prize ({})".format(label, row["prize"]))
        if task_id < first:
            target, prefix = clients[task_id], ""
            target["prize"] = cost(row["prize"])
            target["service_duration"] = service
        else:
            j, delivery = divmod(task_id - first, 2)
            target, prefix = shipments[j], "delivery_" if delivery else "pickup_"
            target[prefix + "service_duration"] = service
            if delivery:  # a shipment's prize sits on its delivery task
                target["prize"] = cost(row["prize"])
        target[prefix + "tw_early"] = early
        if late is not None:
            target[prefix + "tw_late"] = late
    return inst, type_ids


class Built:
    """What PyVRP solves: its data, the request it was built from (costs in
    PyVRP's units), and which vehicle type of the tables each of its own is."""

    def __init__(self, data, instance, type_ids):
        self.data = data
        self.instance = instance
        self.type_ids = type_ids


def _tables_changed(req, tables):
    return any(tables.get(name) != rows for name, rows in req.original.items())


def problem(req, tables):
    """The problem PyVRP solves: `req` with the study's data changes, from `tables`."""
    if req.kind == "vrplib":
        if _tables_changed(req, tables):
            raise evk.InvalidValues(
                "a VRPLIB case takes no data changes: it has no vehicle classes or tags to select by; "
                "use a JSON request (pyvrp-request/1) for a study that changes data")
        return Built(req.data, None, list(range(req.data.num_vehicle_types)))
    inst, type_ids = _changed(req, tables, internal=True)
    inst["vehicle_types"] = [{k: v for k, v in vt.items() if k != "tags"} for vt in inst["vehicle_types"]]
    meta = dict(inst.get("meta") or {})
    meta.setdefault("forbidden", FORBIDDEN)
    inst["meta"] = meta
    return Built(benchmark.build_problem_data(inst), inst, type_ids)


def write(req, tables, path):
    """The request with the study's data changes, as a `pyvrp-request/1` file."""
    if req.kind == "vrplib":
        raise evk.HarnessStop("a VRPLIB case is never changed by a study: export the settings instead")
    inst, _ = _changed(req, tables, internal=False)
    inst["format"] = WRITTEN
    inst["cost_unit"] = req.cost_unit
    inst["time_zero"] = "{:02d}:{:02d}".format(req.time_zero_s // 3600, req.time_zero_s % 3600 // 60)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(inst, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
