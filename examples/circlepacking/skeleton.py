"""Circle packing, n = 26: maximise the sum of radii inside the unit square.

The AlphaEvolve / OpenEvolve / ShinkaEvolve benchmark, in the standard form:
place 26 non-overlapping circles inside `[0, 1]^2` so that the sum of their
radii is as large as possible. Published reference points are in the README;
none of them is a claim about this repository.

Everything above the fence is frozen and is the anti-gaming boundary:

* `repair()` is the referee. It clamps every centre into the square, clamps
  every radius to its distance from the nearest wall, and then shrinks
  overlapping pairs until nothing overlaps. Radii only ever go *down*, so a
  proposal cannot buy score by cheating -- but it is never voided either. The
  score is the sum of radii of the repaired packing.
* `violations()` measures the *raw* proposal, before repair, and reports three
  continuous quantities. They become penalty terms in `evolvekit.yaml`. A
  candidate that proposes a slightly overlapping packing therefore scores just
  below an identical clean one instead of scoring nothing, which is the whole
  point of the penalties-not-`None` rule.
* `descriptors()` reports two behaviour numbers that say nothing about
  quality: how unequal the radii are, and how many circles are pressed against
  the boundary. The archive uses the first as a grid axis.

Below the fence, `construct_packing()` decides the packing. It may compute a
closed-form arrangement, or run a local search -- `TIME_BUDGET_S` is the
budget it should respect; the evaluator's stage timeout is the hard limit and
is several times larger, so a candidate that ignores the constant is killed
rather than rewarded.

Standard library only, and no numpy: the point of the example is that the
search space is a heuristic, not a linear-algebra library.
"""

from __future__ import annotations

import math
import time

__all__ = [
    "N_CIRCLES",
    "TIME_BUDGET_S",
    "wall_clearance",
    "VIOLATION_TOLERANCE",
    "repair",
    "violations",
    "sum_radii",
    "descriptors",
    "deadline",
    "expired",
    "construct_packing",
]

N_CIRCLES = 26
"""The benchmark's n. Proposing a different number is a `count_violation`."""

TIME_BUDGET_S = 2.0
"""Seconds `construct_packing()` should give itself for any local search.

Advisory, not enforced: the hard limit is the stage timeout in
`evolvekit.yaml`, which is set well above this. A candidate that overruns the
budget is not cheating, it is slow -- and the stage will kill it.
"""

TOUCHING_TOLERANCE = 1e-9
"""How close to a wall counts as touching it, for the boundary descriptor."""

VIOLATION_TOLERANCE = 1e-9
"""Slack below which a violation is not a violation.

Two circles that touch exactly are legal, and "exactly" in floating point
is a few ulps either side. Without this the seed itself reports an overlap
of 1e-15 and is penalised for arithmetic. Tao et al. make the same point
from the other direction: an evaluator without a tolerance is an evaluator
that can be gamed by floating-point noise."""


def _clamp_unit(value: float) -> float:
    return 0.0 if value < 0.0 else (1.0 if value > 1.0 else value)


def wall_clearance(x: float, y: float) -> float:
    """Distance from `(x, y)` to the nearest side of the unit square."""
    return min(x, y, 1.0 - x, 1.0 - y)


def _finite(value: object, fallback: float = 0.0) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback
    return number if math.isfinite(number) else fallback


def _normalise(centers, radii) -> tuple[list[tuple[float, float]], list[float]]:
    """Coerce whatever came back into `N_CIRCLES` finite (centre, radius) pairs.

    Missing circles are padded at the corner with radius zero, which is worth
    nothing and blocks nothing; extras are dropped. Either way the count is
    reported as a violation, so the search still feels the mistake.
    """
    points: list[tuple[float, float]] = []
    sizes: list[float] = []
    for index in range(min(len(centers), len(radii))):
        try:
            x, y = centers[index]
        except (TypeError, ValueError):
            continue
        points.append((_clamp_unit(_finite(x, 0.0)), _clamp_unit(_finite(y, 0.0))))
        sizes.append(max(0.0, _finite(radii[index], 0.0)))
    while len(points) < N_CIRCLES:
        points.append((0.0, 0.0))
        sizes.append(0.0)
    return points[:N_CIRCLES], sizes[:N_CIRCLES]


def repair(centers, radii) -> tuple[list[tuple[float, float]], list[float]]:
    """Make the packing feasible by shrinking, never by growing or moving.

    One pass over the pairs is enough: after `(i, j)` has been fixed the two
    radii only ever decrease again, so the constraint cannot be re-broken.
    Circles of radius zero are skipped -- a point is not a circle, and letting
    a degenerate padding entry shrink a real neighbour would punish a candidate
    for the referee's own bookkeeping.
    """
    points, sizes = _normalise(centers, radii)
    for index, (x, y) in enumerate(points):
        sizes[index] = min(sizes[index], wall_clearance(x, y))
    for i in range(N_CIRCLES):
        if sizes[i] <= 0.0:
            continue
        for j in range(i + 1, N_CIRCLES):
            if sizes[j] <= 0.0:
                continue
            distance = math.dist(points[i], points[j])
            overlap = sizes[i] + sizes[j]
            if overlap > distance:
                factor = distance / overlap
                sizes[i] *= factor
                sizes[j] *= factor
    return points, sizes


def violations(centers, radii) -> dict[str, float]:
    """Three continuous measures of how wrong the *raw* proposal was.

    All three are zero for a proposal that was already legal, and all three
    grow smoothly with the mistake, so a near-miss ranks just below a clean
    packing rather than being thrown away.
    """
    count = min(len(centers), len(radii))
    points: list[tuple[float, float]] = []
    sizes: list[float] = []
    bounds = 0.0
    for index in range(count):
        try:
            x, y = centers[index]
        except (TypeError, ValueError):
            bounds += 1.0
            continue
        x, y = _finite(x, -1.0), _finite(y, -1.0)
        radius = max(0.0, _finite(radii[index], 0.0))
        # Outside the square at all, or sticking out of it.
        bounds += max(0.0, -x) + max(0.0, x - 1.0)
        bounds += max(0.0, -y) + max(0.0, y - 1.0)
        bounds += max(0.0, radius - wall_clearance(_clamp_unit(x), _clamp_unit(y)))
        points.append((_clamp_unit(x), _clamp_unit(y)))
        sizes.append(radius)

    overlap = 0.0
    for i in range(len(points)):
        for j in range(i + 1, len(points)):
            if sizes[i] <= 0.0 or sizes[j] <= 0.0:
                continue
            overlap += max(
                0.0, sizes[i] + sizes[j] - math.dist(points[i], points[j])
            )
    return {
        "overlap_violation": _above_tolerance(overlap),
        "bounds_violation": _above_tolerance(bounds),
        "count_violation": float(abs(count - N_CIRCLES)),
    }


def _above_tolerance(value: float) -> float:
    return 0.0 if value <= VIOLATION_TOLERANCE else value - VIOLATION_TOLERANCE


def sum_radii(radii) -> float:
    """The objective. Maximise."""
    return float(sum(radii))


def descriptors(centers, radii) -> dict[str, float]:
    """Behaviour, not quality: what *shape* of packing this candidate makes.

    `radius_variance` separates "26 equal circles" from "a few big ones and a
    lot of filler" -- two families that can score alike and cannot be improved
    the same way. `boundary_fraction` is how much of the packing is pressed
    against the walls. Neither is monotone in the objective, which is what an
    archive axis needs.
    """
    if not radii:
        return {"radius_variance": 0.0, "boundary_fraction": 0.0}
    mean = sum(radii) / len(radii)
    variance = sum((r - mean) ** 2 for r in radii) / len(radii)
    touching = sum(
        1
        for (x, y), r in zip(centers, radii)
        if r > 0.0 and abs(wall_clearance(x, y) - r) <= TOUCHING_TOLERANCE
    )
    return {
        "radius_variance": variance,
        "boundary_fraction": touching / len(radii),
    }


def deadline(seconds: float = TIME_BUDGET_S) -> float:
    """A monotonic timestamp `seconds` from now, for a time-bounded search."""
    return time.monotonic() + max(0.0, seconds)


def expired(when: float) -> bool:
    """True once `when` has passed. Use it as a local search's stop condition."""
    return time.monotonic() >= when


# EVOLVE-BLOCK-START
def construct_packing():
    """Return `(centers, radii)` for 26 circles in the unit square.

    `centers` is a sequence of `(x, y)` pairs and `radii` a sequence of floats,
    both of length 26 and in the same order. The fixed part will clamp and
    shrink whatever comes back until it is legal, and will separately record
    how much clamping and shrinking it had to do.

    Seed arrangement: a 5x5 grid of radius-0.1 circles, which exactly fills the
    square, plus one circle dropped into the first interstice, where the space
    left between four touching circles allows a radius of
    `0.1 * (sqrt(2) - 1)`. That sums to 2.5414214 -- a clean, deterministic
    starting point with no search in it at all, and about 3.6 % below the best
    published figure.

    Ideas worth trying: unequal radii (the optimum is not a uniform grid), a
    hexagonal or row-based arrangement, and a time-bounded local search that
    perturbs the centres and re-derives the radii. `deadline()` / `expired()`
    are available for the last of those, and `TIME_BUDGET_S` is the budget.
    """
    centers = []
    radii = []
    for i in range(5):
        for j in range(5):
            centers.append((0.1 + 0.2 * i, 0.1 + 0.2 * j))
            radii.append(0.1)
    centers.append((0.2, 0.2))
    radii.append(0.1 * (math.sqrt(2.0) - 1.0))
    return centers, radii
# EVOLVE-BLOCK-END
