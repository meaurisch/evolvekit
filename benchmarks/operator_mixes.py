"""Which model-free operators are worth their share? Whole runs, equal budgets.

    python benchmarks/operator_mixes.py            # both regimes, five seeds each
    python benchmarks/operator_mixes.py --seeds 3

Two stand-in solvers, each a command line that knows nothing about evolvekit:

far      the defaults are far from the optimum; 10 parameters of which 4
         matter; no noise; 6 generations x 6 children.
mature   a solver somebody has already tuned: 25 parameters, the defaults close
         to good, most random settings harmful, a noisy objective; 13 x 6.

Reported is the TRUE cost (noise-free) of the configuration each run called its
best -- what a held-out test would see, not what the search saw. The numbers in
the README's operator section come from this script.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path
from statistics import fmean

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evolvekit.config import build_config  # noqa: E402
from evolvekit.search.driver import Driver  # noqa: E402

# -- regime "far" ----------------------------------------------------------

FAR_PARAMS = {
    "neighbours": {"type": "int", "low": 10, "high": 120, "default": 60},
    "penalty": {"type": "float", "low": 1e2, "high": 1e6, "default": 1e5, "log": True},
    "exhaustive": {"type": "bool", "default": True},
    "init": {"type": "choice", "choices": ["greedy", "savings", "sweep"], "default": "greedy"},
    **{f"inert_{i:02d}": {"type": "float", "low": 0.0, "high": 1.0, "default": 0.5} for i in range(6)},
}
FAR_SOLVER = (
    "import argparse, json, math\n"
    "p = argparse.ArgumentParser()\n"
    "p.add_argument('--neighbours', type=int); p.add_argument('--penalty', type=float)\n"
    "p.add_argument('--exhaustive'); p.add_argument('--init')\n"
    "for i in range(6): p.add_argument('--inert-%02d' % i, type=float)\n"
    "a = p.parse_args()\n"
    "cost = 1000 + 3 * abs(a.neighbours - 30) + 40 * abs(math.log10(a.penalty) - 3)\n"
    "cost += (30 if a.exhaustive == 'true' else 0) + {'greedy': 25, 'savings': 0, 'sweep': 10}[a.init]\n"
    "print(json.dumps({'cost': cost}))\n"
)


def far_true_cost(v: dict) -> float:
    import math

    cost = 1000 + 3 * abs(v["neighbours"] - 30) + 40 * abs(math.log10(v["penalty"]) - 3)
    return cost + (30 if v["exhaustive"] else 0) + {"greedy": 25, "savings": 0, "sweep": 10}[v["init"]]


# -- regime "mature" -------------------------------------------------------

NUM = {f"n{i:02d}": (0.5, 0.5 + d) for i, d in enumerate([0.18, -0.15, 0.10, -0.08, 0.05, 0, 0, 0, 0, 0, 0, 0])}
WEIGHT = [6, 5, 4, 3, 2, 1, 1, 0.5, 0.5, 0, 0, 0]
BOOLS = {f"b{i:02d}": w for i, w in enumerate([-1.5, 0.8, 0.6, 0.5, 0.4, 0.3, 0.3, 0.2, 0.2, 0.1, 0.0, 0.0])}
MODES = {"std": 0.0, "fast": 0.4, "deep": -0.3}
MATURE_PARAMS = {
    **{k: {"type": "float", "low": 0.0, "high": 1.0, "default": d} for k, (d, _) in NUM.items()},
    **{k: {"type": "bool", "default": True} for k in BOOLS},
    "mode": {"type": "choice", "choices": list(MODES), "default": "std"},
}
MATURE_SOLVER = f"""
import argparse, json, random
NUM = {NUM!r}; WEIGHT = {WEIGHT!r}; BOOLS = {BOOLS!r}; MODES = {MODES!r}
p = argparse.ArgumentParser()
for k in NUM: p.add_argument("--" + k, type=float)
for k in BOOLS: p.add_argument("--" + k)
p.add_argument("--mode")
a = vars(p.parse_args())
gap = sum(w * abs(a[k] - NUM[k][1]) for k, w in zip(NUM, WEIGHT))
gap += sum(c for k, c in BOOLS.items() if a[k] == "false") + MODES[a["mode"]]
noise = random.Random(str(sorted(a.items()))).gauss(0, 0.15)
print(json.dumps({{"cost": 100.0 + gap + noise}}))
"""


def mature_true_cost(v: dict) -> float:
    gap = sum(w * abs(v[k] - NUM[k][1]) for k, w in zip(NUM, WEIGHT))
    return 100.0 + gap + sum(c for k, c in BOOLS.items() if not v[k]) + MODES[v["mode"]]


REGIMES = {
    "far": (FAR_PARAMS, FAR_SOLVER, far_true_cost, 6, {
        "lhs only": {"param_lhs": 1.0},
        "local only": {"param_local": 1.0},
        "mix": {"param_lhs": 0.2, "param_local": 0.4, "param_tpe": 0.3, "param_cross": 0.1},
    }),
    "mature": (MATURE_PARAMS, MATURE_SOLVER, mature_true_cost, 13, {
        "lhs only": {"param_lhs": 1.0},
        "local only": {"param_local": 1.0},
        "mix": {"param_lhs": 0.15, "param_local": 0.45, "param_tpe": 0.3, "param_cross": 0.1},
    }),
}


def best_of_run(params: dict, solver: str, operators: dict, generations: int, seed: int) -> dict:
    base = Path(tempfile.mkdtemp(prefix="operator-mixes-"))
    (base / "solver.py").write_text(solver, encoding="utf-8")
    config = build_config(
        {
            "problem": {"parameters": params},
            "evaluate": {
                "stages": [
                    {"id": "static", "kind": "builtin-static"},
                    {"id": "full", "kind": "command", "kpis_from": "stdout", "timeout": 60,
                     "command": f'"{sys.executable}" solver.py {{params}}'},
                ],
                "score": {"objective": "cost", "direction": "minimize"},
            },
            "search": {"operators": operators, "children_per_generation": 6, "generations": generations,
                       "seed": seed, "novelty": {"behavioural": "off"}},
            "budget": {"max_full_evals_per_day": 1000},
            "stop": {"patience": 100},
        },
        base_dir=base,
    )
    return Driver(config, run_dir=base / "run", log=lambda message: None).run().best.params


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seeds", type=int, default=5)
    parser.add_argument("--regime", choices=[*REGIMES, "all"], default="all")
    args = parser.parse_args()
    for name, (params, solver, true_cost, generations, mixes) in REGIMES.items():
        if args.regime not in (name, "all"):
            continue
        defaults = {k: v["default"] for k, v in params.items()}
        print(f"\n{name}: defaults cost {true_cost(defaults):.2f}; {generations} generations x 6 children")
        for label, operators in mixes.items():
            costs = [true_cost(best_of_run(params, solver, operators, generations, s)) for s in range(args.seeds)]
            print(f"  {label:11s} mean {fmean(costs):8.2f}   per seed {[round(c, 2) for c in costs]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
