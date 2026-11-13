"""Seeded Latin Hypercube sampling -- the one v1 mechanism worth carrying over.

Stdlib only and fully reproducible from `seed`. Variant 0 is always the base
point, so a sweep can never be worse than the parameters it started from.

Phase A ships this unused: the bin-packing skeleton declares no tunable
parameters, so the `param_lhs` operator in `operators.py` is a stub. The
sampler itself is tested and ready for the first skeleton that does.
"""

from __future__ import annotations

import random
from typing import Any, Mapping, Sequence

__all__ = ["latin_hypercube", "rows_for_params", "sweep_params"]


def latin_hypercube(
    ranges: Mapping[str, Sequence[float]], n_samples: int, seed: int | None = None
) -> list[dict[str, float]]:
    """`n_samples` points, one per stratum per dimension, shuffled across dims."""
    if n_samples < 1 or not ranges:
        return []
    rng = random.Random(seed)
    columns: dict[str, list[float]] = {}
    for key in sorted(ranges):
        bounds = ranges[key]
        if len(bounds) != 2:
            raise ValueError(f"range for {key!r} must be [min, max]")
        low, high = float(bounds[0]), float(bounds[1])
        if not low < high:
            raise ValueError(f"range for {key!r} must satisfy min < max, got {bounds}")
        strata = [
            low + ((i + rng.random()) / n_samples) * (high - low)
            for i in range(n_samples)
        ]
        rng.shuffle(strata)
        columns[key] = strata
    return [{key: columns[key][i] for key in columns} for i in range(n_samples)]


def sweep_params(
    base: Mapping[str, Any],
    ranges: Mapping[str, Sequence[float]],
    n_variants: int = 6,
    seed: int | None = None,
) -> list[dict[str, Any]]:
    """`n_variants` parameter dicts: the base point followed by LHS samples.

    Only keys present in both `base` and `ranges` are swept; the rest are held
    at their base values. Integer base values stay integers.
    """
    variants: list[dict[str, Any]] = [dict(base)]
    keys = sorted(set(base) & set(ranges))
    if n_variants <= 1 or not keys:
        return [dict(base) for _ in range(max(1, n_variants))]

    samples = latin_hypercube({k: ranges[k] for k in keys}, n_variants - 1, seed)
    return variants + rows_for_params(ranges, base, samples)


def rows_for_params(
    ranges: Mapping[str, Sequence[float]],
    base: Mapping[str, Any],
    rows: Sequence[Mapping[str, float]],
) -> list[dict[str, Any]]:
    """Each hypercube row as a configuration of the block's constants: the
    keys present in both `base` and `ranges` from the row, rounded as
    `sweep_params` rounds them (an integer base value stays an integer, the
    rest to six decimals); the others held at their base values."""
    keys = sorted(set(base) & set(ranges))
    out: list[dict[str, Any]] = []
    for sample in rows:
        variant = dict(base)
        for key in keys:
            base_value = base[key]
            raw = sample[key]
            variant[key] = (
                int(round(raw))
                if isinstance(base_value, int) and not isinstance(base_value, bool)
                else round(raw, 6)
            )
        out.append(variant)
    return out
