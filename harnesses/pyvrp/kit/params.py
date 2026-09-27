"""PyVRP's settings, as this harness offers them.

They are the 27 parameters of the benchmark's solver front end
(benchmarks/pyvrp_hard/solve.py): the same names, types, defaults and checks,
and the same choice of which local-search operators are switches. Every
default is PyVRP 0.14.0's own, so a study that tunes nothing runs PyVRP as it
ships. `tests/test_harness_pyvrp.py` checks PARAMETERS and OPERATOR_ORDER
against solve.py.

Settings measured in cost units (COST_UNIT_SETTINGS) are scaled with the
costs PyVRP sees (see request.RESOLUTION), so each setting means what it
means for the request as written.
"""

from __future__ import annotations

import math
import re
import time

import evk_harness as evk

# name, type, default, one line of help. Defaults are PyVRP 0.14.0's.
PARAMETERS = (
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
# to their families. `None` marks an operator that is always on: each is the
# only way to reach a modelling feature (shipments, reloads, prizes, groups).
OPERATOR_ORDER = (
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
"""An edge a routing profile may not use is 1e7 metres and 1e7 seconds long;
a penalty above 1e7 per unit would bring a bad start solution within sight of
int64 overflow inside PyVRP (solve.py)."""

COST_UNIT_SETTINGS = ("min_penalty", "max_penalty", "weight_wait_time")
"""Settings in cost units per unit of something else: a second of lateness, a
kilogram too much, a second of waiting. Multiplied by the request's cost
scale before PyVRP sees them."""

KINDS = {name: kind for name, kind, _, _ in PARAMETERS}
DEFAULTS = {name: default for name, _, default, _ in PARAMETERS}


def _coerce(name, kind, value):
    if kind is bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in ("true", "1", "on", "yes"):
            return True
        if isinstance(value, str) and value.strip().lower() in ("false", "0", "off", "no"):
            return False
        if isinstance(value, (int, float)) and value in (0, 1):
            return bool(value)
        raise evk.InvalidValues("{} must be true or false, got {!r}".format(name, value))
    if isinstance(value, bool):
        raise evk.InvalidValues("{} must be a number, got {!r}".format(name, value))
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise evk.InvalidValues("{} must be a number, got {!r}".format(name, value)) from None
    if not math.isfinite(number):
        raise evk.InvalidValues("{} must be finite, got {!r}".format(name, value))
    if kind is int:
        if number != int(number):
            raise evk.InvalidValues("{} must be a whole number, got {!r}".format(name, value))
        return int(number)
    return number


def _validate(p):
    """The constraints PyVRP enforces, plus the ones it does not (a negative
    weight_wait_time passes silently), as sentences naming the setting."""

    def need(ok, message):
        if not ok:
            raise evk.InvalidValues(message)

    def at_least(name, least):
        need(p[name] >= least, "{} must be at least {}, got {}".format(name, least, p[name]))

    def share(name):
        need(0.0 <= p[name] <= 1.0, "{} must lie between 0 and 1, got {}".format(name, p[name]))

    at_least("num_iters_no_improvement", 0)
    at_least("history_length", 1)
    at_least("solutions_between_updates", 1)
    at_least("penalty_increase", 1.0)
    share("penalty_decrease")
    share("target_feasible")
    share("feas_tolerance")
    at_least("min_penalty", 0.0)
    need(p["max_penalty"] >= p["min_penalty"],
         "max_penalty ({}) must not be below min_penalty ({})".format(p["max_penalty"], p["min_penalty"]))
    need(p["max_penalty"] <= MAX_PENALTY_CEILING,
         "max_penalty must be at most {:g} (int64 overflow inside PyVRP), got {}".format(MAX_PENALTY_CEILING, p["max_penalty"]))
    at_least("weight_wait_time", 0.0)
    at_least("num_neighbours", 1)
    at_least("min_perturbations", 0)
    need(p["min_perturbations"] <= p["max_perturbations"],
         "min_perturbations ({}) must not exceed max_perturbations ({})".format(p["min_perturbations"], p["max_perturbations"]))
    for name, kind, _, _ in PARAMETERS:
        if kind is int:
            need(p[name] <= 2**31 - 1, "{} must be below 2^31, got {}".format(name, p[name]))


def resolve(settings):
    """Every parameter: PyVRP's defaults, overridden by `settings`. An
    unknown name or an invalid value raises evk.InvalidValues."""
    resolved = dict(DEFAULTS)
    for name, value in (settings or {}).items():
        if name not in KINDS:
            raise evk.InvalidValues("{!r} is not a PyVRP setting of this harness; known: {}".format(
                name, ", ".join(sorted(KINDS))))
        resolved[name] = _coerce(name, KINDS[name], value)
    _validate(resolved)
    return resolved


def resolve_operators(params):
    """Names of the operators to hand to PyVRP, in PyVRP's order."""
    return [op for op, flag in OPERATOR_ORDER if flag is None or params[flag]]


class Deadline:
    """Stopping criterion with a deadline fixed BEFORE `pyvrp.solve` is
    called, so building the neighbourhood and the first solution count
    against the time limit too (PyVRP's MaxRuntime starts later)."""

    def __init__(self, deadline):
        self.deadline = deadline

    def __call__(self, best_cost):
        return time.perf_counter() > self.deadline


def solve_params(params, cost_scale=1, callbacks=None):
    """A `pyvrp.SolveParams` for `params` (from `resolve`), with the settings
    in cost units multiplied by `cost_scale`, and the search's `callbacks`
    (a `pyvrp.IteratedLocalSearchCallbacks`)."""
    import pyvrp
    import pyvrp.search

    operators = []
    for name in resolve_operators(params):
        operator = getattr(pyvrp.search, name, None)
        if operator is None:
            raise evk.HarnessStop("the installed PyVRP has no operator {}; this harness is for PyVRP 0.14".format(name))
        operators.append(operator)
    return pyvrp.SolveParams(
        ils=pyvrp.IteratedLocalSearchParams(
            num_iters_no_improvement=params["num_iters_no_improvement"],
            history_length=params["history_length"],
            exhaustive_on_best=params["exhaustive_on_best"],
            callbacks=callbacks,
        ),
        penalty=pyvrp.PenaltyParams(
            solutions_between_updates=params["solutions_between_updates"],
            penalty_increase=params["penalty_increase"],
            penalty_decrease=params["penalty_decrease"],
            target_feasible=params["target_feasible"],
            feas_tolerance=params["feas_tolerance"],
            min_penalty=params["min_penalty"] * cost_scale,
            max_penalty=params["max_penalty"] * cost_scale,
        ),
        neighbourhood=pyvrp.search.NeighbourhoodParams(
            weight_wait_time=params["weight_wait_time"] * cost_scale,
            num_neighbours=params["num_neighbours"],
            symmetric_proximity=params["symmetric_proximity"],
        ),
        operators=operators,
        perturbation=pyvrp.search.PerturbationParams(
            min_perturbations=params["min_perturbations"],
            max_perturbations=params["max_perturbations"],
        ),
    )


def _snake(name):
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def discover():
    """The installed PyVRP's version and settings, read from its parameter
    classes and its operators -- what `harness check` compares the harness
    with, to report drift."""
    import dataclasses
    import importlib.metadata
    import inspect

    import pyvrp
    import pyvrp.search

    found = {}
    for cls in (pyvrp.IteratedLocalSearchParams, pyvrp.PenaltyParams):
        for field in dataclasses.fields(cls):
            if field.name != "callbacks":
                found[field.name] = {"default": field.default}
    for params in (pyvrp.search.NeighbourhoodParams(), pyvrp.search.PerturbationParams()):
        for name in dir(params):
            if not name.startswith("_") and not callable(getattr(params, name)):
                found[name] = {"default": getattr(params, name)}
    default_on = {operator.__name__ for operator in pyvrp.search.OPERATORS}
    switchable = {op: flag for op, flag in OPERATOR_ORDER if flag}
    always = {op for op, flag in OPERATOR_ORDER if flag is None}
    bases = tuple(getattr(pyvrp.search, b) for b in ("BinaryOperator", "UnaryOperator") if hasattr(pyvrp.search, b))
    for name in dir(pyvrp.search):
        operator = getattr(pyvrp.search, name)
        if not (inspect.isclass(operator) and bases and issubclass(operator, bases)) or operator in bases:
            continue
        if name in always:
            continue
        flag = switchable.get(name, "use_" + _snake(name))
        found[flag] = {"default": name in default_on}
    return {"version": importlib.metadata.version("pyvrp"), "settings": found}
