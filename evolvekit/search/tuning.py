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

All three are arithmetic on dicts, need no model and cost no tokens. A
candidate is judged by the deepest stage it finished: with `normalize:
baseline` a screening stage and the full stage are both "percent of the
baseline", so a configuration screened out early still tells the estimator
where not to look.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from evolvekit.space import Parameter, ParameterSpace

__all__ = ["Observation", "propose_tpe", "GOOD_FRACTION", "MIN_OBSERVATIONS"]

GOOD_FRACTION = 0.25
"""The share of observations modelled as "good". The usual choice; with thirty
observations that is seven or eight configurations, enough for a density."""

MIN_OBSERVATIONS = 8
"""Below this there is nothing to estimate a density from; the caller falls
back to a local step."""

CANDIDATES = 48
"""Draws from the "good" density per proposal; the best ratio is proposed."""

PRIOR_WEIGHT = 1.0
"""The declared range counts as this many observations in every density, so
no region is ever ruled out by a handful of samples."""


@dataclass(frozen=True)
class Observation:
    """One configuration and how it did. Higher `fitness` is better."""

    values: Mapping[str, Any]
    fitness: float


def propose_tpe(
    space: ParameterSpace,
    observations: Sequence[Observation],
    rng: random.Random,
    *,
    taken: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any] | None:
    """The configuration, among `CANDIDATES` drawn from the good density, where
    good configurations are most over-represented. `None` when there are too
    few observations to say."""
    usable = [o for o in observations if all(p.name in o.values for p in space)]
    if len(usable) < MIN_OBSERVATIONS:
        return None
    ranked = sorted(usable, key=lambda o: -o.fitness)
    n_good = max(2, math.ceil(GOOD_FRACTION * len(ranked)))
    good, bad = ranked[:n_good], ranked[n_good:]

    densities = {p.name: (_Density(p, good), _Density(p, bad)) for p in space}
    seen = [dict(v) for v in taken] + [dict(o.values) for o in usable]
    best: tuple[float, dict[str, Any]] | None = None
    for _ in range(CANDIDATES):
        values = {p.name: densities[p.name][0].sample(rng) for p in space}
        if values in seen:
            continue
        ratio = sum(
            math.log(densities[p.name][0].pdf(values[p.name]))
            - math.log(densities[p.name][1].pdf(values[p.name]))
            for p in space
        )
        if best is None or ratio > best[0]:
            best = (ratio, values)
    return best[1] if best else None


class _Density:
    """One parameter's distribution among a set of observations.

    A number: a Parzen mixture on the parameter's own 0..1 scale (so a log
    parameter is modelled in its logarithm) -- one Gaussian per observation
    plus the uniform prior. A boolean or a choice: smoothed counts.
    """

    def __init__(self, parameter: Parameter, observations: Sequence[Observation]) -> None:
        self.parameter = parameter
        self.n = len(observations)
        if parameter.numeric:
            self.points = [parameter.to_unit(o.values[parameter.name]) for o in observations]
            self.bandwidth = _bandwidth(self.points)
        else:
            categories = list(parameter.categories)
            counts = {repr(c): PRIOR_WEIGHT / len(categories) for c in categories}
            for o in observations:
                counts[repr(o.values[parameter.name])] = counts.get(repr(o.values[parameter.name]), 0.0) + 1.0
            total = sum(counts.values())
            self.categories = categories
            self.weights = [counts[repr(c)] / total for c in categories]

    def sample(self, rng: random.Random) -> Any:
        parameter = self.parameter
        if not parameter.numeric:
            return rng.choices(self.categories, weights=self.weights, k=1)[0]
        if rng.random() < PRIOR_WEIGHT / (self.n + PRIOR_WEIGHT):
            return parameter.from_unit(rng.random())
        centre = rng.choice(self.points)
        for _ in range(20):  # a truncated Gaussian, by rejection
            unit = rng.gauss(centre, self.bandwidth)
            if 0.0 <= unit <= 1.0:
                return parameter.from_unit(unit)
        return parameter.from_unit(min(1.0, max(0.0, centre)))

    def pdf(self, value: Any) -> float:
        parameter = self.parameter
        if not parameter.numeric:
            return self.weights[self.categories.index(value)] if value in self.categories else 1e-12
        unit = parameter.to_unit(value)
        total = PRIOR_WEIGHT  # the uniform prior has density 1 on [0, 1]
        for point in self.points:
            z = (unit - point) / self.bandwidth
            total += math.exp(-0.5 * z * z) / (self.bandwidth * math.sqrt(2.0 * math.pi))
        return max(total / (self.n + PRIOR_WEIGHT), 1e-12)


def _bandwidth(points: Sequence[float]) -> float:
    """Scott's rule on the unit scale, kept between a twentieth and a third of
    the range: narrower would memorise eight points, wider would say nothing."""
    if len(points) < 2:
        return 0.25
    mean = sum(points) / len(points)
    spread = math.sqrt(sum((p - mean) ** 2 for p in points) / (len(points) - 1))
    return min(0.33, max(0.05, 1.06 * spread * len(points) ** -0.2))
