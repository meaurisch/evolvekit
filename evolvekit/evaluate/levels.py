"""Goals in order of importance, as one score every consumer already reads.

`evaluate.score.levels` says "first the most deliveries per hour, two plans
within 1 % counting as equal; then the least waiting". The rule is
lexicographic: *A is better than B if, at the first level where they differ by
more than that level's tolerance, A is better*. The search, though, ranks by
one finite number -- the archive, promotion, parent sampling, patience and the
estimator all compare `score` -- so the levels are compiled into one, anchored
at the seed's values `b_i`:

    q_i   = clamp(round(s_i * (v_i - b_i) / t_i), -K, K)       levels 1 .. L-1
    x_L   = 0.5 * tanh(s_L * (v_L - b_L) / scale_L)             the last level
    score = sum_i q_i * B ** (L - 1 - i)  +  x_L                 (i from 1)

with `s_i` = +1 to maximise and -1 to minimise, `t_i` the level's absolute
tolerance, `scale_L` a tenth of the seed's last value (1 when that is 0) and
the base B = 2K + 2. One step on a level outweighs every possible difference
below it: the levels below span at most 2K steps each and the last level
less than one. The base is 2K + 2 and not 2K + 1 because `tanh` of a large
difference is exactly 1.0 in floating point -- with 2K + 1, a candidate one
step better on level 1 tied with one that was K steps better on every level
below it. So:

* the seed scores exactly 0, and the anchor never moves: it is kept in
  `work/levels.json` (per stage, like `reference.json`), and a resumed run
  scores against the same one;
* values are compared in steps of the tolerance counted from the seed's
  value. Two values in the same step count as equal -- which means two values
  just either side of a step boundary count as different, however close;
* a difference of more than K = 10,000 steps saturates, and `status` warns
  when a candidate's does;
* at most four levels: the integer part then stays below 2**53, exact in
  double precision, and the last level keeps a resolution finer than a
  thousandth of its range.

A relative tolerance is a fraction of the seed's value; when that value is 0
there is nothing to take a fraction of, and the tolerance is used as an
absolute one.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from evolvekit.config import LevelConfig

__all__ = ["K", "BASE", "Anchor", "make_anchor", "level_steps", "level_score"]

K = 10_000
"""How many tolerance steps a level can differ by before it saturates."""

BASE = 2 * K + 2
"""What one step on a level is worth in steps of the level below it."""


@dataclass(frozen=True)
class Anchor:
    """What the levels are measured from: the seed's value on each level, each
    level's tolerance in the KPI's own units, and the last level's scale."""

    values: dict[str, float]
    tolerances: dict[str, float] = field(default_factory=dict)
    scale: float = 1.0

    def to_json(self) -> dict[str, Any]:
        return {"values": dict(self.values), "tolerances": dict(self.tolerances), "scale": self.scale}

    @staticmethod
    def from_json(payload: Mapping[str, Any]) -> "Anchor":
        return Anchor(
            values={str(k): float(v) for k, v in (payload.get("values") or {}).items()},
            tolerances={str(k): float(v) for k, v in (payload.get("tolerances") or {}).items()},
            scale=float(payload.get("scale", 1.0)),
        )


def make_anchor(levels: Sequence[LevelConfig], values: Mapping[str, float]) -> Anchor:
    """The anchor for `values` -- in a run, the seed's aggregated KPIs."""
    tolerances: dict[str, float] = {}
    for level in levels[:-1]:
        assert level.tolerance is not None  # the config requires one on every level but the last
        base = float(values[level.kpi])
        tolerances[level.kpi] = (
            level.tolerance * abs(base) if level.relative and base != 0 else level.tolerance
        )
    last = float(values[levels[-1].kpi])
    return Anchor(
        values={level.kpi: float(values[level.kpi]) for level in levels},
        tolerances=tolerances,
        scale=0.1 * abs(last) if last != 0 else 1.0,
    )


def _sign(level: LevelConfig) -> float:
    return 1.0 if level.direction == "maximize" else -1.0


def level_steps(levels: Sequence[LevelConfig], anchor: Anchor, values: Mapping[str, float]) -> list[int]:
    """`q_i` for every level but the last: how many tolerance steps better
    (positive) or worse than the anchor, clamped to [-K, K]."""
    steps = []
    for level in levels[:-1]:
        gain = _sign(level) * (float(values[level.kpi]) - anchor.values[level.kpi])
        steps.append(int(max(-K, min(K, round(gain / anchor.tolerances[level.kpi])))))
    return steps


def level_score(levels: Sequence[LevelConfig], anchor: Anchor, values: Mapping[str, float]) -> float:
    """The one finite score the levels compile to (see the module docstring)."""
    count = len(levels)
    total = 0.0
    for index, step in enumerate(level_steps(levels, anchor, values)):
        total += step * BASE ** (count - 2 - index)
    last = levels[-1]
    gain = _sign(last) * (float(values[last.kpi]) - anchor.values[last.kpi])
    return total + 0.5 * math.tanh(gain / anchor.scale)
