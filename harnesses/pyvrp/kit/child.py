"""PyVRP's search in a process of its own, as kit/solving.py starts it.

    python child.py PROBLEM SETTINGS SEED DEADLINE

PROBLEM is a pickled `pyvrp.ProblemData`, SETTINGS a JSON file holding
`{"values": ..., "scale": ...}` for params.solve_params, and DEADLINE the
wall-clock time (`time.time()`) at which the search is to stop. It prints JSON
lines: `{"best": ROUTES}` for the first plan and every better one,
`{"iterations": N}` about twice a second, and `{"done": true, "iterations": N,
"best": ROUTES}` at the end. ROUTES is `[[vehicle_type, [[activity_type,
index], ...]], ...]`: each route's visits without its start and end depot,
which is what `pyvrp.Route` takes to build the same route again.
"""

import json
import os
import pickle
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # the harness: evk_harness, kit

import pyvrp  # noqa: E402

from kit import params  # noqa: E402

REPORT_EVERY_S = 0.5


def routes_of(solution):
    routes = []
    for route in solution.routes():
        visits = list(route)
        if visits and visits[0].is_depot():
            visits = visits[1:]
        if visits and visits[-1].is_depot():
            visits = visits[:-1]
        routes.append([route.vehicle_type(), [[int(a.type), a.idx] for a in visits]])
    return routes


def say(message):
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()


class Reports(pyvrp.IteratedLocalSearchCallbacks):
    def __init__(self):
        super().__init__()
        self.iterations = 0
        self.said = time.perf_counter()

    def on_start(self, ils):
        say({"best": routes_of(ils.initial_solution)})

    def on_best(self, best):
        say({"best": routes_of(best)})

    def on_iteration(self, current, candidate, best, cost_evaluator):
        self.iterations += 1
        now = time.perf_counter()
        if now - self.said >= REPORT_EVERY_S:
            self.said = now
            say({"iterations": self.iterations})


def main(argv):
    problem, settings, seed, deadline = argv[1], argv[2], int(argv[3]), float(argv[4])
    with open(problem, "rb") as handle:
        data = pickle.load(handle)
    with open(settings, "r", encoding="utf-8") as handle:
        settings = json.load(handle)
    solve_params = params.solve_params(settings["values"], settings["scale"], callbacks=Reports())
    stop = params.Deadline(time.perf_counter() + (deadline - time.time()))
    result = pyvrp.solve(data, stop=stop, seed=seed, collect_stats=False, display=False, params=solve_params)
    say({"done": True, "iterations": int(result.num_iterations), "best": routes_of(result.best)})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
