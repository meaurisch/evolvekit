"""Stage evaluator for the circle-packing example.

    python evaluate.py --candidate <file.py> --inputs full --out kpis.json

Loads the candidate module, calls its `construct_packing()`, measures the raw
proposal, repairs it with the skeleton's fixed referee, and writes the KPI JSON
the cascade reads.

There is only one instance -- the unit square with n = 26 -- so `--inputs` is
accepted for contract compatibility and used only to name the run in the log
line. See the README for why this example has no hold-out.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

INPUT_SETS = ("static", "full")


def min_pairwise_gap(centers, radii) -> float:
    """Smallest `distance - r_i - r_j` over all pairs of real circles.

    Zero means some pair is exactly touching, which is what a packing with no
    slack left in it looks like. A visibly positive value is unclaimed space:
    every circle could grow a little and nothing would collide.
    """
    points = [(c, r) for c, r in zip(centers, radii) if r > 0.0]
    if len(points) < 2:
        return 0.0
    gaps = (
        math.dist(points[i][0], points[j][0]) - points[i][1] - points[j][1]
        for i in range(len(points))
        for j in range(i + 1, len(points))
    )
    return max(0.0, min(gaps))


def boundary_contacts(module, centers, radii) -> int:
    """How many circles are pressed against a wall, to within a tolerance."""
    return sum(
        1
        for (x, y), r in zip(centers, radii)
        if r > 0.0 and abs(module.wall_clearance(x, y) - r) <= module.TOUCHING_TOLERANCE
    )


def _feedback(
    module,
    *,
    build_s: float,
    gap: float,
    contacts: int,
    radii,
    raw: dict,
    construction_errors: float,
) -> str:
    """Prose for the next prompt's "Evaluator notes".

    The three things `sum_radii` cannot say and the 2026-08-23 run needed said:
    whether the packing has slack left in it, how much of it is pinned to the
    walls, and -- the one that matters most here -- how much of TIME_BUDGET_S
    the candidate actually spent. Every one of that run's fifteen candidates
    spent under a millisecond, because each of them wrote an arrangement down
    rather than searching for one, and nothing ever told them so.
    """
    budget = float(getattr(module, "TIME_BUDGET_S", 0.0) or 0.0)
    used = (100.0 * build_s / budget) if budget > 0 else 0.0
    lines = [
        f"construct_packing() ran for {build_s:.3f}s, which is {used:.1f}% of the "
        f"{budget:g}s TIME_BUDGET_S it is allowed."
    ]
    if budget > 0 and used < 5.0:
        lines.append(
            "That is essentially none of it. A candidate that returns a "
            "written-down arrangement leaves the entire budget unspent; a "
            "perturb / repair / accept loop running until "
            "expired(deadline(TIME_BUDGET_S)) would take thousands of samples "
            "in the same time."
        )
    positive = [r for r in radii if r > 0.0]
    if positive:
        lines.append(
            f"{len(positive)} circle(s) with radius > 0; radii range "
            f"{min(positive):.5f} to {max(positive):.5f}."
        )
    lines.append(
        f"Minimum gap between any two circles after repair: {gap:.6f}. "
        + (
            "Some pair is exactly touching, so the packing is locally tight."
            if gap <= 1e-9
            else "That is unclaimed space -- every circle could grow by roughly "
            "half of it before anything collided."
        )
    )
    lines.append(
        f"{contacts} of {len(radii)} circle(s) are pressed against a wall. "
        "Published packings near 2.635 press most of their circles against the "
        "boundary and use several different radii; the 5x5 grid seed presses 16."
    )
    if construction_errors:
        lines.append("construct_packing() raised; the packing scored is empty.")
    dirty = [k for k, v in raw.items() if v]
    if dirty:
        lines.append(
            "The raw proposal was illegal and was repaired before scoring: "
            + ", ".join(f"{k}={raw[k]:.6g}" for k in sorted(dirty))
            + ". Repair only ever shrinks, so that violation cost real score."
        )
    return "\n".join(lines)


def load_candidate(path: Path):
    spec = importlib.util.spec_from_file_location("evolvekit_candidate", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load candidate module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in ("construct_packing", "repair", "violations", "descriptors"):
        if not hasattr(module, name):
            raise AttributeError(f"candidate does not define {name}()")
    return module


def evaluate(module, seed: int = 0) -> dict[str, object]:
    """One packing, measured twice: as proposed, and as repaired.

    `seed` seeds the module-level `random` generator before
    `construct_packing()` is called. A candidate that searches with
    `random.random()` therefore takes a different trajectory per seed, which is
    what makes a `seeds: N` stage measure a *candidate* rather than one roll of
    its dice. A candidate that builds a fixed arrangement ignores this
    entirely and reports a coefficient of variation of exactly zero -- which is
    itself a useful thing for the run to be able to see.
    """
    random.seed(seed)
    started = time.perf_counter()
    construction_errors = 0.0
    try:
        centers, radii = module.construct_packing()
        centers = list(centers)
        radii = list(radii)
    except Exception:  # noqa: BLE001 - a crash is a counted incident, not a void
        construction_errors = 1.0
        centers, radii = [], []
    build_s = time.perf_counter() - started

    raw = module.violations(centers, radii)
    fixed_centers, fixed_radii = module.repair(centers, radii)
    behaviour = module.descriptors(fixed_centers, fixed_radii)
    positive = [r for r in fixed_radii if r > 0.0]
    gap = min_pairwise_gap(fixed_centers, fixed_radii)
    contacts = boundary_contacts(module, fixed_centers, fixed_radii)

    return {
        # Prose beside the numbers. `main()` lifts it out of the KPI mapping
        # before writing the JSON, because KPIs are numbers.
        "text_feedback": _feedback(
            module,
            build_s=build_s,
            gap=gap,
            contacts=contacts,
            radii=fixed_radii,
            raw=raw,
            construction_errors=construction_errors,
        ),
        "sum_radii": module.sum_radii(fixed_radii),
        # How close the tightest pair came to touching, after repair. Zero means
        # at least one pair is exactly in contact -- the shape of a packing with
        # nowhere left to grow. A large value is slack the search left behind.
        "min_pairwise_gap": gap,
        "boundary_contacts": float(contacts),
        "overlap_violation": raw["overlap_violation"],
        "bounds_violation": raw["bounds_violation"],
        "count_violation": raw["count_violation"],
        "construction_errors": construction_errors,
        "radius_variance": behaviour["radius_variance"],
        "boundary_fraction": behaviour["boundary_fraction"],
        "circles": float(len(positive)),
        "min_radius": min(positive) if positive else 0.0,
        "max_radius": max(positive) if positive else 0.0,
        # One entry per circle, largest first: a list KPI, so the behaviour
        # signature compares packings circle by circle rather than by their sum.
        "radii_sorted": sorted((round(r, 12) for r in fixed_radii), reverse=True),
        "build_s": build_s,
        "runtime_s": time.perf_counter() - started,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--inputs", required=True, help="comma-separated stage names")
    parser.add_argument("--out", required=True)
    parser.add_argument(
        "--seed",
        type=int,
        default=0,
        help="seeds the module-level `random` generator before the candidate "
        "runs. The framework passes {seed} on a `seeds: N` stage.",
    )
    args = parser.parse_args(argv)

    unknown = [
        name
        for name in args.inputs.split(",")
        if name.strip() and name.strip() not in INPUT_SETS
    ]
    if unknown:
        raise SystemExit(
            f"unknown input set(s) {unknown}; known sets are {list(INPUT_SETS)}"
        )

    module = load_candidate(Path(args.candidate))
    kpis = evaluate(module, args.seed)
    # `text_feedback` rides beside `kpis`, not inside it: KPIs are numbers.
    feedback = str(kpis.pop("text_feedback", ""))

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps({"kpis": kpis, "text_feedback": feedback}, indent=2),
        encoding="utf-8",
        newline="\n",
    )
    print(
        f"{args.inputs} (seed {args.seed}): sum of radii {kpis['sum_radii']:.6f} over "
        f"{int(kpis['circles'])} circle(s), overlap "
        f"{kpis['overlap_violation']:.3g}, bounds {kpis['bounds_violation']:.3g}, "
        f"built in {kpis['build_s']:.2f}s"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
