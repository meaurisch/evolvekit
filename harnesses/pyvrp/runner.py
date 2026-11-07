"""The PyVRP harness: PyVRP 0.14 behind the evolvekit harness SDK
(evk_harness.py, beside this file).

It runs under the Python that has PyVRP installed. A case is a delivery
request -- JSON in the benchmark's format, or a VRPLIB file -- shown to a
study as tables (kit/request.py); `solve` rebuilds the request from those
tables, so the study's data changes reach PyVRP, and solves it within the time
limit; `solution_tables` turns the plan into routes, visits, unassigned tasks
and a summary (kit/plan.py). Everything else -- data changes, constraints,
KPIs, guardrails, exports, exit codes -- is the SDK's.
"""

import copy
import faulthandler
import sys
import time

import evk_harness as evk
from kit import params, plan, request

DEFAULT_TIME_LIMIT_S = 60.0


def read_case(path):
    req = request.read(path)
    tables = request.tables(req)
    req.original = copy.deepcopy(tables)
    return evk.Case(tables, native=req, summary=request.summary(tables))


def write_case(case, path):
    request.write(case.native, case.tables, path)


def apply_lever(case, name, rows, value, mode=None):
    if name != "time_windows":
        raise evk.HarnessStop("runner.py has no code for the lever {!r}".format(name))
    if case.native.kind == "vrplib":
        raise evk.InvalidValues("a VRPLIB case takes no data changes; use a JSON request (pyvrp-request/1)")
    request.change_windows(rows, value, mode)


def solve(case, settings, time_limit_s, seed):
    import pyvrp

    values = params.resolve(settings)
    built = request.problem(case.native, case.tables)
    solve_params = params.solve_params(values, case.native.scale)
    limit = float(time_limit_s or DEFAULT_TIME_LIMIT_S)
    # If PyVRP does not come back from the limit, say where it is: the Python
    # stack on stderr at 1.4 times the limit, before a stage timeout kills it.
    faulthandler.dump_traceback_later(max(5.0, 1.4 * limit), repeat=False, file=sys.stderr)
    started = time.perf_counter()
    try:
        result = pyvrp.solve(
            built.data, stop=params.Deadline(started + limit), seed=int(seed) % 2**32,
            collect_stats=False, display=False, params=solve_params,
        )
    finally:
        faulthandler.cancel_dump_traceback_later()
    return plan.Solved(result.best, built, iterations=result.num_iterations,
                       runtime_s=time.perf_counter() - started)


def solution_tables(case, solution):
    return plan.tables(case.native, solution)


def discover():
    return params.discover()


if __name__ == "__main__":
    evk.main(globals())
