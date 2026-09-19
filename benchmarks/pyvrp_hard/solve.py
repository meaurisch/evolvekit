"""Command-line front end of the solver: the ONLY thing a tuner sees.

    python solve.py --instance PATH --time-limit SECONDS --seed INT
                    [--out FILE] [--params-json FILE_OR_STRING]
                    [--num-neighbours 50] [--use-swap33 true] ...

Why a command line and not a Python API
---------------------------------------
A tuning framework should not need to know that the solver is written in
Python, let alone import it. This program behaves like a solver in any other
language: flags in, exit code and one line of JSON out. Everything a tuner
may touch is a flag; everything else (the instance format, the matrices, the
operators PyVRP needs to reach a modelling feature at all) is not.

The contract
------------
* Every flag defaults to PyVRP 0.14.0's own default, so running without
  parameter flags IS running PyVRP's defaults; `resolve_operators` returns
  exactly `pyvrp.search.OPERATORS` then. The tests pin both statements.
* `--params-json` takes the same parameters as a JSON object (a file name or
  the JSON text itself), keys spelled like the flags with underscores.
  Explicit flags win over the JSON, the JSON wins over the defaults.
* An invalid value -- out of range, wrong type, `min_perturbations` above
  `max_perturbations`, an unknown key -- ends the program with exit code 2
  and ONE line on stderr that says what to change. No traceback, and no
  solver time spent: parameters are checked before PyVRP is even imported.
* `--time-limit` bounds the whole `pyvrp.solve` call: neighbourhood
  computation, construction of the initial solution and the search. PyVRP's
  own `MaxRuntime` starts its clock at the first iteration, after the first
  two, so a private stopping criterion with a deadline fixed before the call
  is used instead. Loading the instance and building the matrices happens
  before the clock starts and is reported as `load_s`.
* The last line of stdout is one JSON object (also written to `--out`).
  Exit code 0 whenever such a result was produced, feasible or not.
* `objective` is the cost of the best feasible solution. If none was found
  it is `offset + priced violations` under the fixed price list of
  `instance.infeasibility_pricing` -- finite, above every feasible cost, and
  still smaller for smaller violations.

Which operators are flags
-------------------------
PyVRP 0.14.0 ships 23 local-search operators, 19 of them on by default.
Flags exist for the ones the MODEL does not depend on:

* the nine pure exchange moves -- Relocate2, Relocate3, Swap11, Swap21,
  Swap22, Swap31, Swap32, Swap33, SwapTails. Relocate1 is always on: without
  it the number of stops on a route can only change in pairs.
* three compound moves whose effect the always-on primitives can reproduce
  in two steps: ReplaceOptionalClient (= RemoveOptionalClient +
  InsertOptionalClient), ReplaceOptionalShipment (likewise) and
  RelocateAlternative (= ReplaceGroup + Relocate1).

Always on, because each is the only way to reach a feature: RelocatePickup,
RelocateDelivery and RelocateShipment (shipments), RelocateWithDepot and
RemoveAdjacentDepot (reloads), Remove-/InsertOptionalClient and
Remove-/InsertOptionalShipment (prize collecting), ReplaceGroup (groups).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
import time
from pathlib import Path

__all__ = ["PARAMETERS", "ParameterError", "main", "resolve_operators", "resolve_parameters"]

HERE = Path(__file__).resolve().parent
PROG = "solve.py"

# name, type, default, one line of help. Defaults are PyVRP 0.14.0's.
PARAMETERS: tuple[tuple[str, type, object, str], ...] = (
    ("num_iters_no_improvement", int, 150_000,
     "iterations without improvement before the search restarts from the best solution"),
    ("history_length", int, 300, "late-acceptance history length"),
    ("exhaustive_on_best", bool, True, "exhaustive local search on every new best solution"),
    ("solutions_between_updates", int, 500, "registrations between penalty updates"),
    ("penalty_increase", float, 1.5, "penalty multiplier when too few solutions are feasible"),
    ("penalty_decrease", float, 0.9, "penalty multiplier when enough solutions are feasible"),
    ("target_feasible", float, 0.65, "target share of feasible solutions"),
    ("feas_tolerance", float, 0.05, "tolerated deviation from the target share"),
    ("min_penalty", float, 0.1, "lower clamp of the violation penalties"),
    ("max_penalty", float, 100_000.0, "upper clamp of the violation penalties"),
    ("weight_wait_time", float, 0.2, "weight of waiting time in the proximity measure"),
    ("num_neighbours", int, 50, "size of the granular neighbourhood"),
    ("symmetric_proximity", bool, True, "symmetrise the proximity measure"),
    ("min_perturbations", int, 1, "fewest perturbations per iteration"),
    ("max_perturbations", int, 25, "most perturbations per iteration"),
    ("use_relocate2", bool, True, "operator Relocate2"),
    ("use_relocate3", bool, False, "operator Relocate3"),
    ("use_swap11", bool, True, "operator Swap11"),
    ("use_swap21", bool, True, "operator Swap21"),
    ("use_swap22", bool, True, "operator Swap22"),
    ("use_swap31", bool, False, "operator Swap31"),
    ("use_swap32", bool, False, "operator Swap32"),
    ("use_swap33", bool, False, "operator Swap33"),
    ("use_swap_tails", bool, True, "operator SwapTails"),
    ("use_relocate_alternative", bool, True, "operator RelocateAlternative"),
    ("use_replace_optional_client", bool, True, "operator ReplaceOptionalClient"),
    ("use_replace_optional_shipment", bool, True, "operator ReplaceOptionalShipment"),
)

# PyVRP's default order, with the four default-off operators slotted in next
# to their families. `None` marks an operator that is always on. Filtering
# this list with the default flags gives `pyvrp.search.OPERATORS` exactly.
OPERATOR_ORDER: tuple[tuple[str, str | None], ...] = (
    ("Relocate1", None),
    ("Relocate2", "use_relocate2"),
    ("Relocate3", "use_relocate3"),
    ("Swap11", "use_swap11"),
    ("Swap21", "use_swap21"),
    ("Swap22", "use_swap22"),
    ("Swap31", "use_swap31"),
    ("Swap32", "use_swap32"),
    ("Swap33", "use_swap33"),
    ("SwapTails", "use_swap_tails"),
    ("RelocateAlternative", "use_relocate_alternative"),
    ("RelocatePickup", None),
    ("RelocateDelivery", None),
    ("RelocateWithDepot", None),
    ("RemoveAdjacentDepot", None),
    ("RemoveOptionalClient", None),
    ("InsertOptionalClient", None),
    ("ReplaceOptionalClient", "use_replace_optional_client"),
    ("RemoveOptionalShipment", None),
    ("InsertOptionalShipment", None),
    ("ReplaceOptionalShipment", "use_replace_optional_shipment"),
    ("ReplaceGroup", None),
    ("RelocateShipment", None),
)

MAX_PENALTY_CEILING = 1e7
"""An edge a profile may not use is 1e7 metres and 1e7 seconds long. A
penalty above 1e7 per unit would put a bad start solution with a few hundred
such edges within sight of int64 overflow inside PyVRP."""

MAX_SEED = 2**32 - 1


class ParameterError(ValueError):
    """An invalid parameter value. The message is the user-facing line."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # one line, exit code 2, no usage dump
        sys.stderr.write(f"{PROG}: error: {message} (see --help)\n")
        raise SystemExit(2)


def _flag(name: str) -> str:
    return "--" + name.replace("_", "-")


def _coerce(name: str, kind: type, value: object) -> object:
    """`value` as `kind`, or a ParameterError that names the flag."""
    flag = _flag(name)
    if kind is bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in ("true", "1", "on", "yes"):
            return True
        if isinstance(value, str) and value.strip().lower() in ("false", "0", "off", "no"):
            return False
        raise ParameterError(f"{flag} must be true or false, got {value!r}")
    if isinstance(value, bool):
        raise ParameterError(f"{flag} must be a number, got {value!r}")
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ParameterError(f"{flag} must be a number, got {value!r}") from None
    if not math.isfinite(number):
        raise ParameterError(f"{flag} must be finite, got {value!r}")
    if kind is int:
        if number != int(number):
            raise ParameterError(f"{flag} must be a whole number, got {value!r}")
        return int(number)
    return number


def _validate(p: dict) -> None:
    """The constraints PyVRP itself enforces, plus the few it does not (it
    accepts a negative `weight_wait_time` without complaint), as one-line
    messages. Checked here so that nothing reaches PyVRP that would raise."""

    def need(ok: bool, message: str) -> None:
        if not ok:
            raise ParameterError(message)

    def at_least(name: str, least: float) -> None:
        need(p[name] >= least, f"{_flag(name)} must be at least {least}, got {p[name]}")

    def share(name: str) -> None:
        need(0.0 <= p[name] <= 1.0, f"{_flag(name)} must lie in [0, 1], got {p[name]}")

    at_least("num_iters_no_improvement", 0)
    at_least("history_length", 1)
    at_least("solutions_between_updates", 1)
    at_least("penalty_increase", 1.0)
    share("penalty_decrease")
    share("target_feasible")
    share("feas_tolerance")
    at_least("min_penalty", 0.0)
    need(p["max_penalty"] >= p["min_penalty"],
         f"--max-penalty ({p['max_penalty']}) must not be below --min-penalty ({p['min_penalty']})")
    need(p["max_penalty"] <= MAX_PENALTY_CEILING,
         f"--max-penalty must be at most {MAX_PENALTY_CEILING:g} (int64 overflow inside PyVRP), "
         f"got {p['max_penalty']}")
    at_least("weight_wait_time", 0.0)
    at_least("num_neighbours", 1)
    at_least("min_perturbations", 0)
    need(p["min_perturbations"] <= p["max_perturbations"],
         f"--min-perturbations ({p['min_perturbations']}) must not exceed "
         f"--max-perturbations ({p['max_perturbations']})")
    for name, kind, _, _ in PARAMETERS:
        if kind is int:
            need(p[name] <= 2**31 - 1, f"{_flag(name)} must be below 2^31, got {p[name]}")


def _read_params_json(source: str) -> dict:
    text = source
    if not source.lstrip().startswith("{"):
        path = Path(source)
        if not path.is_file():
            raise ParameterError(
                f"--params-json {source!r} is neither a JSON object nor an existing file")
        text = path.read_text(encoding="utf-8")
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError as err:
        raise ParameterError(f"--params-json is not valid JSON: {err.msg} at position {err.pos}") from None
    if not isinstance(loaded, dict):
        raise ParameterError("--params-json must hold a JSON object of parameter names and values")
    return loaded


def resolve_parameters(from_flags: dict, params_json: str | None) -> dict:
    """Defaults, overridden by `--params-json`, overridden by explicit flags.
    Returns the full, validated parameter dict; raises `ParameterError`."""
    kinds = {name: kind for name, kind, _, _ in PARAMETERS}
    resolved = {name: default for name, _, default, _ in PARAMETERS}
    if params_json is not None:
        for key, value in _read_params_json(params_json).items():
            name = str(key).lstrip("-").replace("-", "_")
            if name not in kinds:
                raise ParameterError(
                    f"--params-json has an unknown parameter {key!r}; "
                    f"known: {', '.join(sorted(kinds))}")
            resolved[name] = _coerce(name, kinds[name], value)
    for name, value in from_flags.items():
        if value is not None:
            resolved[name] = _coerce(name, kinds[name], value)
    _validate(resolved)
    return resolved


def resolve_operators(params: dict) -> list[str]:
    """Names of the operators to hand to PyVRP, in PyVRP's order."""
    return [op for op, flag in OPERATOR_ORDER if flag is None or params[flag]]


def _load_sibling(name: str):
    """Import a sibling module by path (its name is too generic for sys.path)."""
    key = f"pyvrp_hard_{name}"
    if key in sys.modules:
        return sys.modules[key]
    spec = importlib.util.spec_from_file_location(key, HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    return module


def _peak_rss_mb() -> float | None:
    """Peak resident set size of this process, or None where the platform
    offers no cheap way to ask. No psutil: stdlib only."""
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            kernel32.K32GetProcessMemoryInfo.argtypes = [
                wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
            kernel32.K32GetProcessMemoryInfo.restype = wintypes.BOOL
            counters = Counters()
            counters.cb = ctypes.sizeof(Counters)
            if not kernel32.K32GetProcessMemoryInfo(
                    kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
                return None
            return round(counters.PeakWorkingSetSize / 2**20, 1)
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return round(peak / (2**20 if sys.platform == "darwin" else 2**10), 1)
    except Exception:  # noqa: BLE001 - a missing number must never cost a result
        return None


class _Deadline:
    """Stopping criterion with a deadline fixed BEFORE `pyvrp.solve` is
    called, so construction time counts against the limit."""

    def __init__(self, deadline: float):
        self.deadline = deadline

    def __call__(self, best_cost: int) -> bool:
        return time.perf_counter() > self.deadline


def _build_parser() -> _Parser:
    parser = _Parser(
        prog=PROG, description="Solve one pyvrp_hard instance with PyVRP 0.14.0.",
        epilog="Every parameter flag defaults to PyVRP's own default.")
    parser.add_argument("--instance", required=True, help="instance JSON file")
    parser.add_argument("--time-limit", required=True,
                        help="seconds for the whole pyvrp.solve call (float)")
    parser.add_argument("--seed", required=True, help=f"random seed, 0..{MAX_SEED}")
    parser.add_argument("--out", help="also write the result JSON to this file")
    parser.add_argument("--params-json", help="parameters as a JSON object: a file or the text")
    parser.add_argument("--diagnostics", action="store_true",
                        help="add static instance checks to the result (slower to load)")
    group = parser.add_argument_group("tunable parameters")
    for name, kind, default, text in PARAMETERS:
        shown = str(default).lower() if kind is bool else default
        group.add_argument(_flag(name), default=None,
                           metavar="{true,false}" if kind is bool else kind.__name__.upper(),
                           help=f"{text} (default: {shown})")
    return parser


def _solution_report(instance: dict, data, best) -> dict:
    """Everything the JSON says about the best solution."""
    from pyvrp import CostEvaluator

    dims = data.num_load_dimensions
    types = instance["vehicle_types"]
    clients = instance["clients"]
    served_clients: set[int] = set()
    served_shipments: set[int] = set()
    per_type: dict[str, int] = {}
    per_class: dict[str, int] = {}
    with_overtime = 0
    for route in best.routes():
        vt = types[route.vehicle_type()]
        per_type[vt["name"]] = per_type.get(vt["name"], 0) + 1
        per_class[vt["class"]] = per_class.get(vt["class"], 0) + 1
        with_overtime += int(route.overtime() > 0)
        for activity in route:
            if activity.is_client():
                served_clients.add(activity.idx)
            elif activity.is_pickup():
                served_shipments.add(activity.idx)

    optional_clients = [i for i, c in enumerate(clients)
                        if not c["required"] and c["group"] is None]
    optional_shipments = [i for i, s in enumerate(instance["shipments"]) if not s["required"]]
    optional_groups = [g for g in instance["groups"] if not g["required"]]
    groups_served = sum(any(i in served_clients for i in g["clients"]) for g in optional_groups)

    pricing = _load_sibling("instance").infeasibility_pricing(instance)
    fixed = CostEvaluator(pricing["load_penalties"], pricing["tw_penalty"], pricing["dist_penalty"])
    penalised = int(fixed.penalised_cost(best))
    feasible = bool(best.is_feasible())
    cost = int(CostEvaluator([0] * dims, 0, 0).cost(best)) if feasible else None
    missing = (best.num_missing_clients() + best.num_missing_groups()
               + best.num_missing_shipments())
    objective = cost if feasible else (
        pricing["offset"] + penalised + missing * pricing["missing_penalty"])
    return {
        "objective": objective,
        "feasible": feasible,
        "cost": cost,
        "penalised_cost": penalised,
        "num_routes": best.num_routes(),
        "num_trips": best.num_trips(),
        "distance": int(best.distance()),
        "duration": int(best.duration()),
        "overtime": int(best.overtime()),
        "routes_with_overtime": with_overtime,
        "fixed_cost": int(best.fixed_vehicle_cost()),
        "distance_cost": int(best.distance_cost()),
        "duration_cost": int(best.duration_cost()),
        "prizes_collected": int(best.prizes()),
        "prizes_uncollected": int(best.uncollected_prizes()),
        "served_optional_clients": sum(i in served_clients for i in optional_clients),
        "unserved_optional_clients": sum(i not in served_clients for i in optional_clients),
        "served_optional_shipments": sum(i in served_shipments for i in optional_shipments),
        "unserved_optional_shipments": sum(i not in served_shipments for i in optional_shipments),
        "served_optional_groups": groups_served,
        "unserved_optional_groups": len(optional_groups) - groups_served,
        "missing_required": missing,
        "time_warp": int(best.time_warp()),
        "excess_load": [int(x) for x in best.excess_load()],
        "excess_distance": int(best.excess_distance()),
        "vehicle_types_used": sorted(per_type),
        "vehicle_classes_used": sorted(per_class),
        "routes_per_class": dict(sorted(per_class.items())),
        "num_vehicles": data.num_vehicles,
    }


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        flags = {name: getattr(args, name) for name, _, _, _ in PARAMETERS}
        params = resolve_parameters(flags, args.params_json)
        time_limit = _coerce("time_limit", float, args.time_limit)
        if not time_limit > 0:
            raise ParameterError(f"--time-limit must be positive, got {args.time_limit}")
        seed = _coerce("seed", int, args.seed)
        if not 0 <= seed <= MAX_SEED:
            raise ParameterError(f"--seed must lie in 0..{MAX_SEED}, got {args.seed}")
        if not Path(args.instance).is_file():
            raise ParameterError(f"--instance {args.instance!r} is not a file")
    except ParameterError as err:
        parser.error(str(err))

    # -- load (outside the time limit) -------------------------------------
    started = time.perf_counter()
    import pyvrp
    import pyvrp.search

    module = _load_sibling("instance")
    try:
        instance = module.load_instance(args.instance)
    except (ValueError, json.JSONDecodeError) as err:
        parser.error(f"--instance: {err}")
    data = module.build_problem_data(instance)
    diagnostics = None
    if args.diagnostics:
        diagnostics = {
            "missing_features": module.missing_features(instance),
            "unservable_stops": module.unservable_stops(data),
        }
    load_s = time.perf_counter() - started

    # -- solve (inside the time limit) -------------------------------------
    dims = data.num_load_dimensions
    plain = pyvrp.CostEvaluator([0] * dims, 0, 0)
    events: list[tuple[float, int]] = []
    clock = {"start": 0.0}

    class Convergence(pyvrp.IteratedLocalSearchCallbacks):
        """Records (elapsed, cost) whenever the best feasible cost improves.
        Cheaper than `collect_stats`, which keeps a record per iteration."""

        def on_start(self, ils):  # noqa: D102
            self.on_best(ils.initial_solution)

        def on_best(self, best):  # noqa: D102
            if best.is_feasible():
                events.append((time.perf_counter() - clock["start"], int(plain.cost(best))))

    solve_params = pyvrp.SolveParams(
        ils=pyvrp.IteratedLocalSearchParams(
            num_iters_no_improvement=params["num_iters_no_improvement"],
            history_length=params["history_length"],
            exhaustive_on_best=params["exhaustive_on_best"],
            callbacks=Convergence(),
        ),
        penalty=pyvrp.PenaltyParams(
            solutions_between_updates=params["solutions_between_updates"],
            penalty_increase=params["penalty_increase"],
            penalty_decrease=params["penalty_decrease"],
            target_feasible=params["target_feasible"],
            feas_tolerance=params["feas_tolerance"],
            min_penalty=params["min_penalty"],
            max_penalty=params["max_penalty"],
        ),
        neighbourhood=pyvrp.search.NeighbourhoodParams(
            weight_wait_time=params["weight_wait_time"],
            num_neighbours=params["num_neighbours"],
            symmetric_proximity=params["symmetric_proximity"],
        ),
        operators=[getattr(pyvrp.search, name) for name in resolve_operators(params)],
        perturbation=pyvrp.search.PerturbationParams(
            min_perturbations=params["min_perturbations"],
            max_perturbations=params["max_perturbations"],
        ),
    )
    clock["start"] = time.perf_counter()
    result = pyvrp.solve(
        data,
        stop=_Deadline(clock["start"] + time_limit),
        seed=seed,
        collect_stats=False,
        display=False,
        params=solve_params,
    )
    runtime_s = time.perf_counter() - clock["start"]

    report = _solution_report(instance, data, result.best)
    # Best feasible cost known at 10 %, 20 %, ... of the limit. The last
    # entry is the final result, including what the last iteration found
    # after the deadline passed (PyVRP only checks between iterations).
    convergence: list[int | None] = []
    for tenth in range(1, 11):
        seen = [cost for at, cost in events if at <= time_limit * tenth / 10]
        convergence.append(min(seen) if seen else None)
    convergence[-1] = report["cost"]

    output = {
        **report,
        "iterations": int(result.num_iterations),
        "runtime_s": round(runtime_s, 3),
        "load_s": round(load_s, 3),
        "time_limit_s": time_limit,
        "time_to_first_feasible_s": round(events[0][0], 3) if events else None,
        "convergence": convergence,
        "instance": instance["name"],
        "seed": seed,
        "params": params,
        "operators": resolve_operators(params),
        "pyvrp_version": _pyvrp_version(),
        "peak_rss_mb": _peak_rss_mb(),
    }
    if diagnostics is not None:
        output["diagnostics"] = diagnostics
    line = json.dumps(output, sort_keys=True, separators=(",", ":"))
    if args.out:
        Path(args.out).write_bytes((line + "\n").encode("utf-8"))
    sys.stdout.write(line + "\n")
    sys.stdout.flush()
    return 0


def _pyvrp_version() -> str:
    import importlib.metadata

    return importlib.metadata.version("pyvrp")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except KeyboardInterrupt:
        sys.stderr.write(f"{PROG}: interrupted\n")
        raise SystemExit(130) from None
    except Exception as err:  # noqa: BLE001 - a foreign solver dies with one line
        sys.stderr.write(f"{PROG}: error: {type(err).__name__}: {err}\n")
        raise SystemExit(1) from None
