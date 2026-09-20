"""A stand-in for YOUR solver. evolvekit never imports this file.

It is here so the example runs anywhere without a compiler; treat it as the
binary you already have -- `./solver`, `java -jar solver.jar`, `solver.exe`.
What matters is only what any such program offers:

    solver --instance FILE --seed N --time-limit SECONDS [--flag value ...]

* flags in; one line of JSON on stdout, the solver's own, with whatever else it
  likes to report next to the cost;
* it logs progress lines before the result;
* it is slow-ish, its result depends on the seed, and now and then it crashes.

The "problem" is a toy (a random travelling-salesman tour improved by 2-opt
with a few knobs), so the numbers mean nothing beyond the example.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--instance", required=True)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--time-limit", type=float, default=2.0)
    p.add_argument("--neighbours", type=int, default=8, help="candidate moves per city")
    p.add_argument("--restart-after", type=int, default=2000, help="stalled moves before a restart")
    p.add_argument("--accept-worse", type=float, default=0.0, help="probability of accepting a worse move")
    p.add_argument("--init", choices=("random", "nearest"), default="random")
    p.add_argument("--or-opt", choices=("true", "false"), default="false")
    a = p.parse_args()

    spec = json.load(open(a.instance, encoding="utf-8"))
    rng = random.Random(a.seed * 1_000_003 + spec["seed"])
    # A real solver's one-in-twenty-five segfault: not reproducible, so a retry helps.
    # (CLI_SOLVER_NO_CRASH=1 switches it off, for the test suite.)
    if not os.environ.get("CLI_SOLVER_NO_CRASH") and random.SystemRandom().random() < spec.get("crash_rate", 0.0):
        sys.stderr.write("solver: fatal: corrupted neighbour list\n")
        return 139

    points = [(random.Random(spec["seed"] + i).random(), random.Random(spec["seed"] - i - 1).random()) for i in range(spec["cities"])]
    n = len(points)

    def dist(i: int, j: int) -> float:
        return math.hypot(points[i][0] - points[j][0], points[i][1] - points[j][1])

    near = [sorted(range(n), key=lambda j, i=i: dist(i, j))[1 : a.neighbours + 1] for i in range(n)]
    if a.init == "nearest":
        tour, left = [0], set(range(1, n))
        while left:
            nxt = min(left, key=lambda j: dist(tour[-1], j))
            tour.append(nxt)
            left.remove(nxt)
    else:
        tour = list(range(n))
        rng.shuffle(tour)

    def length(t: list[int]) -> float:
        return sum(dist(t[k], t[(k + 1) % n]) for k in range(n))

    best, best_len = tour[:], length(tour)
    current, stalled, iterations = best_len, 0, 0
    deadline = time.perf_counter() + a.time_limit
    while time.perf_counter() < deadline:
        iterations += 1
        i = rng.randrange(n)
        j = tour.index(rng.choice(near[tour[i]]))
        lo, hi = sorted((i, j))
        if hi - lo < 2:
            continue
        candidate = tour[:lo + 1] + tour[lo + 1 : hi + 1][::-1] + tour[hi + 1 :]
        if a.or_opt == "true" and rng.random() < 0.3:  # move a short segment elsewhere
            k = rng.randrange(n - 3)
            segment, rest = candidate[k : k + 2], candidate[:k] + candidate[k + 2 :]
            at = rng.randrange(len(rest))
            candidate = rest[:at] + segment + rest[at:]
        cand_len = length(candidate)
        if cand_len < current or rng.random() < a.accept_worse:
            tour, current = candidate, cand_len
        if current < best_len - 1e-12:
            best, best_len, stalled = tour[:], current, 0
            if iterations % 50 == 0:
                print(f"iter {iterations}  best {best_len:.4f}")
        else:
            stalled += 1
            if stalled >= a.restart_after:
                tour, current, stalled = best[:], best_len, 0

    print(json.dumps({"cost": round(best_len, 6), "iterations": iterations, "instance": spec["name"],
                      "settings": {"neighbours": a.neighbours, "init": a.init}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
