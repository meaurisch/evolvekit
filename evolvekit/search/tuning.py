"""Proposing the next configuration from the ones already paid for.

`param_lhs` fills the declared box evenly and never looks at a score. That is
the right first move and the wrong tenth one: with an evaluator that costs an
hour, every result has to inform the next proposal. Three model-free operators
do that over a typed `problem.parameters` space, each for a different reason:

`param_local`   a few parameters of a good configuration moved a little. The
                defaults of a mature solver are already good, so most of what
                can be won is next to them, and a step that changes two
                parameters says something about those two.
`param_cross`   each parameter from one of two good configurations: gains that
                were found separately are tried together.
`param_tpe`     a Tree-structured Parzen Estimator (Bergstra et al., 2011) over
                *every* configuration evaluated so far, the bad ones included.
                It models where good configurations are dense relative to bad
                ones, parameter by parameter, and proposes where that ratio is
                highest -- which is how the search learns that one boolean
                costs five percent without anybody reading a chart.

All three are arithmetic on dicts, need no model and cost no tokens. What
the estimator reads is a rank, not a score: finished candidates by fitness,
then those screened out by a cheaper stage (by how deep they got and that
stage's score), then the raced-out and vetoed ones, then the failed and gated
ones. Only the order matters to a split into good and bad, so a configuration
screened out early tells the estimator where not to look on any scale.

A parameter's `prior` (what an earlier study's winner suggests) is part of
both densities: a narrow bump at its value beside the uniform, weighed by the
prior's weight, so a region near an earlier winner is tried first and no
region is ever ruled out.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from statistics import NormalDist
from typing import Any, Mapping, Sequence

from evolvekit.space import CONSTRAINT_ATTEMPTS, Parameter, ParameterSpace

__all__ = ["Observation", "propose_tpe", "n_good", "MIN_OBSERVATIONS"]

MIN_OBSERVATIONS = 8
"""Below this there is nothing to estimate a density from; the caller falls
back to a local step."""

FROM_GOOD = 24
"""Candidates drawn from the "good" density per proposal."""

FROM_PRIOR = 24
"""Candidates drawn uniformly from the box per proposal. Draws from the good
density alone stay next to the good points; these let the ratio find a region
the good points have not reached yet. The best ratio of all 48 is proposed."""

PRIOR_WEIGHT = 1.0
"""The declared range counts as this many observations in every density, so
no region is ever ruled out by a handful of samples."""

PRIOR_PSEUDO_COUNT = 4.0
"""A parameter's `prior` of weight `w` counts as `4 w` more observations, at
its value, on top of the range's one: at a warm start's 0.5 the earlier winner
counts as two observations, as many as a short run's whole good set."""

PRIOR_BANDWIDTH = 0.1
"""The width of a numeric prior's bump on the unit scale (`PRIOR_BAND`, the
band `param_lhs` draws in around a prior, either side)."""

MIN_BANDWIDTH, MAX_BANDWIDTH = 0.02, 0.5
"""A point's bandwidth is the larger gap to its neighbours, kept between a
fiftieth of the range (two points on one value still spread a little) and a
half (a lone point does not flatten into the uniform)."""

_PHI = NormalDist().cdf
_ROOT_TWO_PI = math.sqrt(2.0 * math.pi)


def n_good(n: int) -> int:
    """How many of `n` ranked observations are modelled as "good": a tenth,
    rounded up, at least two and at most twenty-five (Optuna's split). A long
    run's good set stays the best few, not a quarter of everything."""
    return max(2, min(25, (n + 9) // 10))  # integer ceil(n / 10): 0.1 * 30 is 3.0000000000000004


@dataclass(frozen=True)
class Observation:
    """One configuration and how it did. Higher `fitness` is better; only the
    order counts. The driver passes a rank score (`Driver._observations`:
    minus the position, in four tiers), not a score on the objective's scale."""

    values: Mapping[str, Any]
    fitness: float


def propose_tpe(
    space: ParameterSpace,
    observations: Sequence[Observation],
    rng: random.Random,
    *,
    taken: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any] | None:
    """The configuration, among `FROM_GOOD` candidates drawn from the good
    density and `FROM_PRIOR` drawn uniformly from the box, where good
    configurations are most over-represented. `None` when there are too few
    observations to say. Every configuration in `taken` (the siblings proposed
    so far this generation) counts as a bad observation and is never proposed
    itself."""
    usable = [o for o in observations if all(p.name in o.values for p in space)]
    if len(usable) < MIN_OBSERVATIONS:
        return None
    ranked = sorted(usable, key=lambda o: -o.fitness)
    split = n_good(len(ranked))
    good, bad = ranked[:split], ranked[split:]
    # The constant liar: a sibling already proposed this generation is not yet
    # evaluated, and is told to the estimator as a bad configuration, so parallel
    # proposals spread out. Added after the split, it never changes `n_good`.
    bad += [Observation(v, -math.inf) for v in taken if all(p.name in v for p in space)]

    densities = {p.name: (_Density(p, good), _Density(p, bad)) for p in space}
    seen = [dict(v) for v in taken] + [dict(o.values) for o in usable]
    best: tuple[float, dict[str, Any]] | None = None
    sources = (
        (FROM_GOOD, lambda: {p.name: densities[p.name][0].sample(rng) for p in space}),
        (FROM_PRIOR, lambda: {p.name: p.from_unit(rng.random()) for p in space}),
    )
    # A draw that breaks a constraint (`problem.parameter_constraints`) is not
    # a candidate at all: it is skipped, and up to CONSTRAINT_ATTEMPTS more
    # draws per source are allowed to find its candidates.
    for count, draw in sources:
        rated = 0
        for _ in range(count + (CONSTRAINT_ATTEMPTS if space.constraints else 0)):
            values = draw()
            if values in seen or not space.satisfies(values):
                continue
            ratio = sum(
                math.log(densities[p.name][0].pdf(values[p.name]))
                - math.log(densities[p.name][1].pdf(values[p.name]))
                for p in space
            )
            if best is None or ratio > best[0]:
                best = (ratio, values)
            rated += 1
            if rated >= count:
                break
    return best[1] if best else None


class _Density:
    """One parameter's distribution among a set of observations.

    A number: a Parzen mixture on the parameter's own 0..1 scale (so a log
    parameter is modelled in its logarithm) -- one Gaussian per observation,
    each with its own bandwidth (`_bandwidths`) and truncated to the range,
    plus the prior part: the uniform, and a prior's bump. A boolean or a
    choice: smoothed counts, the prior's pseudo-counts on its value.

    With `n` observations and a prior of weight `w` (0 without one), the
    prior part weighs `W = 1 + 4 w` and the density is
    `(U(x) + 4 w G(x) + sum_i N_i(x)) / (W + n)`. Each Gaussian is
    renormalised by its mass inside [0, 1], `Z = Phi((1 - p) / h) - Phi(-p / h)`,
    so the density integrates to one and is the density `sample` draws from.
    """

    def __init__(self, parameter: Parameter, observations: Sequence[Observation]) -> None:
        self.parameter = parameter
        self.n = len(observations)
        prior_value, weight = parameter.prior if parameter.prior is not None else (None, 0.0)
        self.extra = PRIOR_PSEUDO_COUNT * weight
        """The prior's pseudo-count, `4 w`; the range's is `PRIOR_WEIGHT`."""
        self.weight = PRIOR_WEIGHT + self.extra
        if parameter.numeric:
            # Clamped: a value from a run whose range was wider still sits in this one.
            self.points = [min(1.0, max(0.0, parameter.to_unit(o.values[parameter.name]))) for o in observations]
            self.bandwidths = _bandwidths(self.points)
            self.masses = [_mass(p, h) for p, h in zip(self.points, self.bandwidths)]
            if self.extra:
                self.centre = min(1.0, max(0.0, parameter.to_unit(prior_value)))
                self.centre_mass = _mass(self.centre, PRIOR_BANDWIDTH)
        else:
            categories = list(parameter.categories)
            counts = {repr(c): PRIOR_WEIGHT / len(categories) for c in categories}
            if self.extra and repr(prior_value) in counts:
                counts[repr(prior_value)] += self.extra
            for o in observations:
                counts[repr(o.values[parameter.name])] = counts.get(repr(o.values[parameter.name]), 0.0) + 1.0
            total = sum(counts.values())
            self.categories = categories
            self.weights = [counts[repr(c)] / total for c in categories]

    def sample(self, rng: random.Random) -> Any:
        parameter = self.parameter
        if not parameter.numeric:
            return rng.choices(self.categories, weights=self.weights, k=1)[0]
        if rng.random() < self.weight / (self.n + self.weight):
            if self.extra and rng.random() < self.extra / self.weight:
                return parameter.from_unit(_truncated(rng, self.centre, PRIOR_BANDWIDTH))
            return parameter.from_unit(rng.random())
        index = rng.randrange(self.n)
        return parameter.from_unit(_truncated(rng, self.points[index], self.bandwidths[index]))

    def pdf(self, value: Any) -> float:
        parameter = self.parameter
        if not parameter.numeric:
            return self.weights[self.categories.index(value)] if value in self.categories else 1e-12
        unit = parameter.to_unit(value)
        total = PRIOR_WEIGHT  # the uniform has density 1 on [0, 1]
        if self.extra:
            total += self.extra * _kernel(unit, self.centre, PRIOR_BANDWIDTH, self.centre_mass)
        for point, bandwidth, mass in zip(self.points, self.bandwidths, self.masses):
            total += _kernel(unit, point, bandwidth, mass)
        return max(total / (self.n + self.weight), 1e-12)


def _bandwidths(points: Sequence[float]) -> list[float]:
    """Each point's bandwidth, in the order given: the larger of its gaps to
    its neighbours when the points are sorted with 0 and 1 at the ends, kept
    between `MIN_BANDWIDTH` and `MAX_BANDWIDTH`. Where the good points crowd
    together the density is sharp; a point on its own stays broad."""
    order = sorted(range(len(points)), key=lambda i: points[i])
    ordered = [0.0] + [points[i] for i in order] + [1.0]
    out = [0.0] * len(points)
    for rank, index in enumerate(order, start=1):
        gap = max(ordered[rank] - ordered[rank - 1], ordered[rank + 1] - ordered[rank])
        out[index] = min(MAX_BANDWIDTH, max(MIN_BANDWIDTH, gap))
    return out


def _mass(centre: float, bandwidth: float) -> float:
    """The share of a Gaussian at `centre` with `bandwidth` that lies in [0, 1]."""
    return _PHI((1.0 - centre) / bandwidth) - _PHI(-centre / bandwidth)


def _kernel(unit: float, centre: float, bandwidth: float, mass: float) -> float:
    """The Gaussian's density at `unit`, truncated to [0, 1] and renormalised by its `mass`."""
    z = (unit - centre) / bandwidth
    return math.exp(-0.5 * z * z) / (bandwidth * _ROOT_TWO_PI * mass)


def _truncated(rng: random.Random, centre: float, bandwidth: float) -> float:
    """A draw from the Gaussian truncated to [0, 1], by rejection; after 20
    rejections (at worst, a point on an end with the widest bandwidth, about
    0.0002 % of draws) the centre itself."""
    for _ in range(20):
        unit = rng.gauss(centre, bandwidth)
        if 0.0 <= unit <= 1.0:
            return unit
    return centre
