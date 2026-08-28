"""Stage evaluator for the PyVRP configuration-search example.

    python evaluate.py --candidate <file.py> --inputs metro300 --out kpis.json \
        --seed 42 --time-limit 20

Loads the candidate module (a copy of `skeleton.py` with the evolve block
rewritten), runs its `run_solve()` on every instance in the named input
set(s), and writes the KPI JSON the cascade reads.

Money is reported in **EUR**: the instance is denominated in hundredths of a
cent so that PyVRP's integer `unit_distance_cost` can express EUR 0.30/km, and
a score of 20,431 reads better than one of 204,311,189.

If the interpreter running this file has no usable PyVRP, it hands the job to
`.venv-pyvrp` and exits with that run's status -- see `ensure_pyvrp()` for why
the stage command cannot simply name the right interpreter.

`--seed` and `--time-limit` are explicit arguments rather than constants
because the framework is growing a `{seed}` placeholder for multi-seed stages.
Until it lands, the stage command in the YAML pins them; afterwards only the
`--seed` value changes.

Besides `kpis`, the output file carries a `text_feedback` string. The current
framework reads `payload["kpis"]` and ignores everything else, so the key is
free today and is what the prompt will quote once `text_feedback` lands.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

MIN_PYVRP = (0, 14)
"""The example needs reload depots, overtime, shipments and
`PerturbationParams`, all of which arrived in PyVRP 0.14."""

VENV_NAME = ".venv-pyvrp"
"""Where the example keeps its own interpreter, by convention. See the README."""

_RELAUNCH_FLAG = "EVOLVEKIT_PYVRP_RELAUNCHED"


def _pyvrp_is_usable() -> bool:
    try:
        installed = importlib.metadata.version("pyvrp")
    except importlib.metadata.PackageNotFoundError:
        return False
    try:
        parts = tuple(int(part) for part in installed.split(".")[:2])
    except ValueError:  # pragma: no cover - a non-numeric version string
        return True
    return parts >= MIN_PYVRP


def _find_venv_interpreter() -> Path | None:
    for base in (HERE, *HERE.parents):
        for relative in (("Scripts", "python.exe"), ("bin", "python")):
            candidate = base.joinpath(VENV_NAME, *relative)
            if candidate.is_file():
                return candidate
    return None


def ensure_pyvrp() -> None:
    """Hand the job to the example's own interpreter when this one cannot do it.

    The stage command in the YAML says `python`, because a config cannot carry
    a machine-specific path. On Windows that resolves to the *system*
    interpreter even when a virtual environment is active and first on PATH --
    `subprocess` does not inherit the shell's idea of which `python` is meant.
    Rather than pretend otherwise, the evaluator looks for `.venv-pyvrp` beside
    or above itself and re-runs there. `EVOLVEKIT_PYVRP_RELAUNCHED` stops that
    from happening twice if the venv is itself out of date.
    """
    if _pyvrp_is_usable():
        return
    wanted = ".".join(str(part) for part in MIN_PYVRP)
    if os.environ.get(_RELAUNCH_FLAG):
        raise SystemExit(
            f"{Path(sys.executable).name} has no usable pyvrp >= {wanted}, and "
            f"the {VENV_NAME} it was handed over to has not either"
        )
    interpreter = _find_venv_interpreter()
    if interpreter is None:
        raise SystemExit(
            f"this evaluator needs pyvrp >= {wanted}; {sys.executable} does not "
            f"have it and there is no {VENV_NAME} above {HERE}. Create one:\n"
            f"    python -m venv {VENV_NAME}\n"
            f'    {VENV_NAME}/Scripts/python -m pip install -e ".[dev,pyvrp]"'
        )
    environment = dict(os.environ, **{_RELAUNCH_FLAG: "1"})
    raise SystemExit(
        subprocess.call(
            [str(interpreter), str(Path(__file__).resolve()), *sys.argv[1:]],
            env=environment,
        )
    )

COST_UNITS_PER_EUR = 10_000.0
"""Cost unit of the instance files: one unit is 1e-4 EUR. Every money KPI is
divided by this, so the objective is reported in euro."""

INPUT_SETS: dict[str, tuple[str, ...]] = {
    # The offline demo and the tests: 300 orders, seconds rather than minutes.
    "metro300": ("instance-300-s7.json",),
    "metro300b": ("instance-300-s23.json",),
    # The real thing: 3,000 orders. `metro3000` is the public instance used by
    # both the proxy and the full stage -- they differ in `--time-limit`, not
    # in the instance. `metro3000b` is the private hold-out: same generator,
    # different seed, never used for ranking except through the hold-out gap.
    "metro3000": ("instance-3000-s7.json",),
    "metro3000b": ("instance-3000-s23.json",),
    # Accepted so that a stage may name the static set without special-casing.
    "static": (),
}

REQUIRED_ATTRS = ("run_solve", "build_data", "load_instance", "configure")


def load_candidate(path: Path):
    spec = importlib.util.spec_from_file_location("evolvekit_candidate", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load candidate module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in REQUIRED_ATTRS:
        if not hasattr(module, name):
            raise AttributeError(f"candidate does not define {name}()")
    return module


def _entropy(counts: dict) -> float:
    """Shannon entropy, in bits, of how the routes spread over vehicle types.

    A behaviour descriptor, not a quality one: 0.0 means every route uses the
    same vehicle type, and about 3.6 (log2 of the twelve types) would mean the
    routes are spread evenly across the whole fleet. A configuration that
    reaches the same cost with one big-truck lineage and one that reaches it
    with a mixed fleet are different animals and want different mutations.
    """
    total = sum(counts.values())
    if total <= 0:
        return 0.0
    return -sum(
        (n / total) * math.log2(n / total) for n in counts.values() if n > 0
    )


def _instance_paths(names: list[str]) -> list[Path]:
    paths: list[Path] = []
    for name in names:
        for filename in INPUT_SETS[name]:
            path = HERE / filename
            if not path.is_file():
                raise SystemExit(
                    f"missing instance {path.name}; generate it with\n"
                    f"    python make_instance.py --orders {_orders_of(filename)} "
                    f"--seed {_seed_of(filename)} --out {filename}"
                )
            paths.append(path)
    return paths


def _orders_of(filename: str) -> str:
    return filename.split("-")[1]


def _seed_of(filename: str) -> str:
    return filename.split("-s")[-1].split(".")[0]


def evaluate(module, paths: list[Path], *, seed: int, time_limit: float) -> tuple[dict, str]:
    """Run every instance once and fold the results into one KPI mapping."""
    started = time.perf_counter()
    results = []
    for path in paths:
        instance = module.load_instance(path)
        results.append((path, instance, module.run_solve(
            instance, time_limit=time_limit, seed=seed
        )))

    n = max(1, len(results))

    def mean(key: str) -> float:
        return sum(float(r[key]) for _p, _i, r in results) / n

    fleet: dict[int, int] = {}
    for _p, _i, r in results:
        for vehicle_type, count in r["fleet_mix"].items():
            fleet[int(vehicle_type)] = fleet.get(int(vehicle_type), 0) + int(count)

    infeasible = sum(0.0 if r["feasible"] else 1.0 for _p, _i, r in results)
    excess_load = sum(sum(r["excess_load"]) for _p, _i, r in results) / n

    kpis: dict[str, object] = {
        # -- the objective ------------------------------------------------
        # The PyVRP cost of the best *feasible* solution, in EUR. When no
        # feasible solution was found this is the penalised cost instead --
        # a real number rather than the `inf` PyVRP reports -- and
        # `infeasible` is 1, which the config prices out of contention.
        "objective": mean("objective") / COST_UNITS_PER_EUR,
        "infeasible": infeasible,
        # -- what the routing looks like ----------------------------------
        "distance": mean("distance"),
        "duration": mean("duration"),
        "num_routes": mean("num_routes"),
        "num_trips": mean("num_trips"),
        "fixed_cost": mean("fixed_cost") / COST_UNITS_PER_EUR,
        "distance_cost": mean("distance_cost") / COST_UNITS_PER_EUR,
        "duration_cost": mean("duration_cost") / COST_UNITS_PER_EUR,
        "uncollected_prizes": mean("uncollected_prizes") / COST_UNITS_PER_EUR,
        "clients_visited": mean("clients_visited"),
        # -- violations, all zero for a feasible solution -----------------
        "unassigned_clients": mean("unassigned_clients"),
        "missing_required_clients": mean("missing_required_clients"),
        "missing_groups": mean("missing_groups"),
        "unassigned_shipments": mean("unassigned_shipments"),
        "excess_load": excess_load,
        "excess_distance": mean("excess_distance"),
        "time_warp": mean("time_warp"),
        "overtime": mean("overtime"),
        # -- behaviour, not quality ---------------------------------------
        "mean_route_length": mean("mean_route_length"),
        "fleet_mix_entropy": _entropy(fleet),
        # -- effort --------------------------------------------------------
        "iterations": mean("iterations"),
        "time_used_s": mean("time_used_s"),
        "budget_s": mean("budget_s"),
        "configure_s": mean("configure_s"),
        "config_errors": sum(float(r["config_errors"]) for _p, _i, r in results),
        "runtime_s": time.perf_counter() - started,
        # A list KPI: the best cost at 25/50/75/100 % of the solve budget,
        # in EUR, for the first instance. Lists never reach the score or the
        # archive; they sharpen the behaviour signature, which is what tells
        # a configuration that converged early apart from one that was still
        # improving when the clock ran out.
        "convergence": [
            round(v / COST_UNITS_PER_EUR, 6) if v >= 0 else -1.0
            for v in results[0][2]["convergence"]
        ],
    }
    return kpis, _feedback(results)


def _feedback(results) -> str:
    """A few lines of prose about how the run went, for the next prompt."""
    lines: list[str] = []
    for path, instance, r in results:
        name = instance["meta"]["name"]
        quarters = " -> ".join(
            "n/a" if v < 0 else f"{v / COST_UNITS_PER_EUR:,.0f}" for v in r["convergence"]
        )
        state = "feasible" if r["feasible"] else "INFEASIBLE"
        lines.append(
            f"{name}: {state}, objective EUR {r['objective'] / COST_UNITS_PER_EUR:,.0f} "
            f"after {int(r['iterations']):,} iterations in "
            f"{r['time_used_s']:.1f}s of a {r['budget_s']:.0f}s budget."
        )
        lines.append(f"  best cost at 25/50/75/100 % of the budget: {quarters}")
        if not r["feasible"]:
            lines.append(
                f"  violations: excess load {sum(r['excess_load']):.0f}, time warp "
                f"{r['time_warp']:.0f}s, excess distance {r['excess_distance']:.0f}m, "
                f"{int(r['missing_required_clients'])} required client(s) and "
                f"{int(r['missing_groups'])} group(s) unserved. The penalty schedule "
                f"is the first thing to look at."
            )
        mix = ", ".join(
            f"{instance['vehicle_types'][t]['name']} x{c}"
            for t, c in sorted(r["fleet_mix"].items())
        )
        lines.append(
            f"  {int(r['num_routes'])} route(s) over {int(r['num_trips'])} trip(s), "
            f"{r['mean_route_length']:.1f} clients each; fleet: {mix or 'none'}"
        )
        lines.append(
            f"  dropped {int(r['unassigned_clients'])} optional order(s) worth "
            f"EUR {r['uncollected_prizes'] / COST_UNITS_PER_EUR:,.0f} in prizes; overtime "
            f"{r['overtime']:.0f}s"
        )
        if r["config_errors"]:
            lines.append(f"  configure() was rejected: {r['config_note']}")
        if r["warm_started"]:
            lines.append(
                f"  warm start supplied; configure() itself used "
                f"{r['configure_s']:.1f}s of the budget"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ensure_pyvrp()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--inputs", required=True, help="comma-separated set names")
    parser.add_argument("--out", required=True)
    parser.add_argument("--seed", type=int, default=42, help="PyVRP RNG seed")
    parser.add_argument(
        "--time-limit",
        type=float,
        default=20.0,
        help="requested solve budget in seconds; the skeleton caps it at 900",
    )
    args = parser.parse_args(argv)

    names = [name.strip() for name in args.inputs.split(",") if name.strip()]
    unknown = [name for name in names if name not in INPUT_SETS]
    if unknown:
        raise SystemExit(
            f"unknown input set(s) {unknown}; known sets are {sorted(INPUT_SETS)}"
        )

    module = load_candidate(Path(args.candidate))
    paths = _instance_paths(names)
    if not paths:
        raise SystemExit(f"input set(s) {names} name no instance to solve")

    kpis, feedback = evaluate(
        module, paths, seed=args.seed, time_limit=args.time_limit
    )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps({"kpis": kpis, "text_feedback": feedback}, indent=2),
        encoding="utf-8",
        newline="\n",
    )
    print(feedback)
    return 0


if __name__ == "__main__":
    sys.exit(main())
